from typing import Optional, List, Any, Generator, Tuple
import json
import pathlib
import ctypes
from contextlib import contextmanager

import bpy
import mathutils  # pylint: disable=E0401
from progress_report import ProgressReport

from .. import gltftypes, gltf_buffer


class Mat16(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("f00", ctypes.c_float),
        ("f01", ctypes.c_float),
        ("f02", ctypes.c_float),
        ("f03", ctypes.c_float),
        ("f10", ctypes.c_float),
        ("f11", ctypes.c_float),
        ("f12", ctypes.c_float),
        ("f13", ctypes.c_float),
        ("f20", ctypes.c_float),
        ("f21", ctypes.c_float),
        ("f22", ctypes.c_float),
        ("f23", ctypes.c_float),
        ("f30", ctypes.c_float),
        ("f31", ctypes.c_float),
        ("f32", ctypes.c_float),
        ("f33", ctypes.c_float),
    ]



@contextmanager
def tmp_mode(obj, tmp: str):
    mode = obj.rotation_mode
    obj.rotation_mode = tmp
    try:
        yield
    finally:
        obj.rotation_mode = mode


class Skin:
    def __init__(self, base_dir: pathlib.Path, gltf: gltftypes.glTF, skin: gltftypes.Skin)->None:
        self.base_dir = base_dir
        self.gltf = gltf
        self.skin = skin
        self.inverse_matrices: Any = None

    def get_matrix(self, joint: int)->Any:
        if not self.inverse_matrices:
            self.inverse_matrices = gltf_buffer.get_array(
                self.base_dir, self.gltf, self.skin.inverseBindMatrices, Mat16)
        m = self.inverse_matrices[joint]
        mat = mathutils.Matrix((
            (m.f00, m.f10, m.f20, m.f30),
            (m.f01, m.f11, m.f21, m.f31),
            (m.f02, m.f12, m.f22, m.f32),
            (m.f03, m.f13, m.f23, m.f33)
        ))
        #d = mat.decompose()
        return mat


class Node:
    def __init__(self, index: int, gltf_node: gltftypes.Node, skins: List[Tuple[Skin, int]])->None:
        self.index = index
        self.gltf_node = gltf_node
        self.parent: Optional[Node] = None
        self.children: List[Node] = []
        self.blender_object: Any = None

        self.skin: Optional[Skin] = None
        if len(skins) > 1:
            raise Exception('Multiple skin')
        elif len(skins) == 1:
            self.skin = skins[0][0]
            self.skin_joint = skins[0][1]
        self.blender_armature: Any = None
        self.blender_bone: Any = None

    def __str__(self)->str:
        return f'{self.index}'

    def create_object(self, progress: ProgressReport,
                      collection, meshes: List[Any], mod_v, mod_q)->None:
        name = self.gltf_node.name
        if not name:
            name = '_%03d' % self.index

        # create object
        if self.gltf_node.mesh != -1:
            self.blender_object = bpy.data.objects.new(
                name, meshes[self.gltf_node.mesh])
        else:
            # empty
            self.blender_object = bpy.data.objects.new(name, None)
            self.blender_object.empty_display_size = 0.1
            #self.blender_object.empty_draw_type = 'PLAIN_AXES'
        collection.objects.link(self.blender_object)
        self.blender_object.select_set("SELECT")

        self.blender_object['js'] = json.dumps(self.gltf_node.js, indent=2)

        # parent
        if self.parent:
            self.blender_object.parent = self.parent.blender_object

        if self.gltf_node.translation:
            self.blender_object.location = mod_v(self.gltf_node.translation)

        if self.gltf_node.rotation:
            r = self.gltf_node.rotation
            q = mathutils.Quaternion((r[3], r[0], r[1], r[2]))
            with tmp_mode(self.blender_object, 'QUATERNION'):
                self.blender_object.rotation_quaternion = mod_q(q)

        if self.gltf_node.scale:
            s = self.gltf_node.scale
            self.blender_object.scale = (s[0], s[2], s[1])

        if self.gltf_node.matrix:
            m = self.gltf_node.matrix
            matrix = mathutils.Matrix((
                (m[0], m[4], m[8], m[12]),
                (m[1], m[5], m[9], m[13]),
                (m[2], m[6], m[10], m[14]),
                (m[3], m[7], m[11], m[15])
            ))
            t, q, s = matrix.decompose()
            self.blender_object.location = mod_v(t)
            with tmp_mode(self.blender_object, 'QUATERNION'):
                self.blender_object.rotation_quaternion = mod_q(q)
            self.blender_object.scale = (s[0], s[2], s[1])

        progress.step()

        for child in self.children:
            child.create_object(progress, collection, meshes, mod_v, mod_q)

    # create armature
    def create_armature(self, context, collection, view_layer, is_connect: bool)->None:
        if self.skin:
            skin = self.skin

            if not self.blender_object:
                return
            blender_object = self.blender_object

            #parent_blender_object = node.parent.blender_object if node.parent else None

            node_name = self.gltf_node.name
            if not node_name:
                node_name = '_%03d' % self.index

            if self.parent and self.parent.skin == skin:
                parent_bone = self.parent.blender_bone
                armature = self.parent.blender_armature.data
                self.blender_armature = self.parent.blender_armature
            else:
                parent_bone = None
                # new armature
                skin_name = skin.skin.name
                if skin_name:
                    skin_name = 'armature' + node_name

                armature = bpy.data.armatures.new(skin_name)
                self.blender_armature = bpy.data.objects.new(
                    skin_name, armature)
                collection.objects.link(self.blender_armature)
                self.blender_armature.show_in_front = True
                self.blender_armature.parent = self.blender_object.parent

                # select and edit mode
                self.blender_armature.select_set("SELECT")
                view_layer.objects.active = self.blender_armature
                bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

                m = mathutils.Matrix()
                m.identity()
                self.blender_armature.matrix_world = m
                context.scene.update()

                bpy.ops.object.mode_set(mode='EDIT', toggle=False)

            # create bone
            self.blender_bone = armature.edit_bones.new(node_name)
            self.blender_bone.use_connect = is_connect
            self.blender_bone.parent = parent_bone

            object_pos = blender_object.matrix_world.to_translation()
            #skin_pos = skin.get_matrix(self.skin_joint).inverted().to_translation()
            #print(object_pos, skin_pos)
            self.blender_bone.head = object_pos
            if not self.children:
                self.blender_bone.tail = self.blender_bone.head + \
                    (self.blender_bone.head - self.parent.blender_bone.head)

        def child_is_connect(child_pos)->bool:
            if not self.skin:
                return False
            if len(self.children) == 1:
                return True

            parent_head = mathutils.Vector((0, 0, 0))
            if parent_bone:
                parent_head = parent_bone.head
            parent_dir = (self.blender_bone.head - parent_head).normalized()
            child_dir = (
                child_pos - blender_object.matrix_world.to_translation()).normalized()
            dot = parent_dir.dot(child_dir)
            #print(parent_dir, child_dir, dot)
            return dot > 0.8

        for child in self.children:
            child.create_armature(context, collection, view_layer, child_is_connect(
                child.blender_object.matrix_world.to_translation()))


def load_objects(context, progress: ProgressReport,
                 base_dir: pathlib.Path,
                 meshes: List[Any], gltf: gltftypes.glTF)->List[Any]:
    progress.enter_substeps(len(gltf.nodes)+1, "Loading objects...")

    # collection
    view_layer = context.view_layer
    if view_layer.collections.active:
        collection = view_layer.collections.active.collection
    else:
        collection = context.scene.master_collection.new()
        view_layer.collections.link(collection)

    # setup
    skins = [Skin(base_dir, gltf, skin) for skin in gltf.skins]

    def get_skins(i: int)->Generator[Tuple[Skin, int], None, None]:
        for skin in skins:
            for j, joint in enumerate(skin.skin.joints):
                if joint == i:
                    yield skin, j
                    break
    nodes = [Node(i, gltf_node, [skin for skin in get_skins(i)])
             for i, gltf_node in enumerate(gltf.nodes)]

    # set parents
    for gltf_node, node in zip(gltf.nodes, nodes):
        for child_index in gltf_node.children:
            child = nodes[child_index]
            node.children.append(child)
            child.parent = node
    if nodes[0].parent:
        raise Exception()

    progress.step()

    # setup from root to descendants
    def mod_v(v):
        # return (v[0], v[1], v[2])
        return v

    def mod_q(q):
        # return mathutils.Quaternion(mod_v(q.axis), q.angle)
        return q
    nodes[0].create_object(progress, collection, meshes, mod_v, mod_q)

    # build armature
    nodes[0].create_armature(context, collection, view_layer, False)
    bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

    progress.leave_substeps()
    return [node.blender_object for node in nodes]
