import json
import pathlib
from typing import Set, List

from progress_report import ProgressReport  # , ProgressReportSubstep
import bpy

from .. import gltftypes, blender_io

from .import_manager import ImportManager
from .texture_io import load_textures
from .material_io import load_materials
from .mesh_io import load_meshes
from .node_io import load_objects
from .node import Node


from logging import getLogger  # pylint: disable=C0411
logger = getLogger(__name__)


def _setup_skinning(blender_object: bpy.types.Object,
                    joints, weights, bone_names: List[str],
                    armature_object: bpy.types.Object)->None:
    # create vertex groups
    for bone_name in bone_names:
        blender_object.vertex_groups.new(
            name=bone_name)

    idx_already_done: Set[int] = set()

    # each face
    for poly in blender_object.data.polygons:
        # face vertex index
        for loop_idx in range(poly.loop_start, poly.loop_start + poly.loop_total):
            vert_idx = blender_object.data.loops[loop_idx].vertex_index

            if vert_idx in idx_already_done:
                continue
            idx_already_done.add(vert_idx)

            cpt = 0
            for joint_idx in joints[vert_idx]:
                weight_val = weights[vert_idx][cpt]
                if weight_val != 0.0:
                    # It can be a problem to assign weights of 0
                    # for bone index 0, if there is always 4 indices in joint_ tuple
                    bone_name = bone_names[joint_idx]
                    group = blender_object.vertex_groups[bone_name]
                    group.add([vert_idx], weight_val, 'REPLACE')
                cpt += 1

    # select
    # for obj_sel in bpy.context.scene.objects:
    #    obj_sel.select = False
    #blender_object.select = True
    #bpy.context.scene.objects.active = blender_object

    modifier = blender_object.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = armature_object


def load(context,
         filepath: str,
         yup_to_zup: bool
         )->Set[str]:

    path = pathlib.Path(filepath)
    if not path.exists():
        return {'CANCELLED'}

    with ProgressReport(context.window_manager) as progress:
        progress.enter_substeps(5, "Importing GLTF %r..." % path.name)

        with path.open() as f:
            gltf = gltftypes.from_json(json.load(f))

        manager = ImportManager(path, gltf, yup_to_zup)
        manager.textures.extend(blender_io.load_textures(progress, manager))
        manager.materials.extend(blender_io.load_materials(progress, manager))
        manager.meshes.extend(blender_io.load_meshes(progress, manager))
        nodes = blender_io.load_objects(
            context, progress, manager)

        # skinning
        for node in nodes:
            if node.gltf_node.mesh != -1 and node.gltf_node.skin != -1:
                _, attributes = manager.meshes[node.gltf_node.mesh]
                skin = gltf.skins[node.gltf_node.skin]
                bone_names = [
                    nodes[joint].bone_name for joint in skin.joints]
                _setup_skinning(node.blender_object, attributes.joints,
                                attributes.weights, bone_names,
                                nodes[skin.skeleton].blender_armature)

        # remove empties
        roots = [node for node in enumerate(nodes) if not node[1].parent]
        if len(roots) != 1 and roots[0][0] != 0:
            raise Exception()

        def remove_empty(node: Node):
            for i in range(len(node.children)-1, -1, -1):
                child = node.children[i]
                remove_empty(child)

            if node.children:
                print(f'{node} children {len(node.children)}')
                return
            if node.blender_armature:
                print(f'{node} has {node.blender_armature}')
                return
            if node.blender_object.data:
                print(f'{node} has {node.blender_object}')
                return

            # remove empty
            print('remove', node)
            bpy.data.objects.remove(node.blender_object, do_unlink=True)
            if node.parent:
                node.parent.children.remove(node)

        remove_empty(roots[0][1])

        context.scene.update()

        progress.leave_substeps("Finished")
        return {'FINISHED'}
