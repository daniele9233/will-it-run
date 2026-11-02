"""Blender background script: decimation, high-to-low PBR/normal bake, Draco/WebP."""
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector


def progress(percent, phase):
    print("WIR_PROGRESS:" + json.dumps({"percent": percent, "phase": phase}), flush=True)


def main():
    config = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text())
    directory = Path(config["directory"])
    size = config["texture_size"]
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    progress(10, "web_import")
    bpy.ops.import_scene.gltf(filepath=str(directory / "source.glb"))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("The model contains no mesh.")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.join()
    high = bpy.context.object
    high.name = "DetailedSource"
    high.data.calc_loop_triangles()
    original_triangles = len(high.data.loop_triangles)
    low = high.copy()
    low.data = high.data.copy()
    low.name = "WebModel"
    bpy.context.collection.objects.link(low)
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    progress(20, "web_simplify")
    # Imported glTF duplicates vertices at UV/normal seams. Weld the geometry before
    # decimation and create a fresh render UV layer, otherwise thousands of tiny
    # inherited islands waste the atlas and the selected-to-active bake.
    while low.data.uv_layers:
        low.data.uv_layers.remove(low.data.uv_layers[0])
    topology = bmesh.new()
    topology.from_mesh(low.data)
    bmesh.ops.remove_doubles(topology, verts=list(topology.verts), dist=0.000001)
    topology.to_mesh(low.data)
    topology.free()
    # Clean the generated surface into a connected mesh before reducing it. The
    # raw reconstruction can contain non-manifold edges that stop collapse early.
    low.data.remesh_voxel_size = max(low.dimensions) / 384
    low.data.remesh_voxel_adaptivity = 0
    bpy.ops.object.voxel_remesh()
    low.data.calc_loop_triangles()
    before_decimation = len(low.data.loop_triangles)
    if before_decimation > config["triangles"]:
        modifier = low.modifiers.new("Web triangle budget", "DECIMATE")
        modifier.ratio = config["triangles"] / before_decimation
        modifier.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    low.data.calc_loop_triangles()
    triangles = len(low.data.loop_triangles)
    if triangles > config["triangles"] * 1.02:
        raise RuntimeError(f"Could not meet the Web triangle budget: {triangles} triangles.")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.002)
    bpy.ops.object.mode_set(mode="OBJECT")
    low.data.uv_layers.active.active_render = True
    low.data.materials.clear()
    material = bpy.data.materials.new("WebPBR")
    material.use_nodes = True
    low.data.materials.append(material)
    for polygon in low.data.polygons:
        polygon.material_index = 0
    nodes = material.node_tree.nodes
    shader = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    target = nodes.new("ShaderNodeTexImage")
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 8
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "CUDA"
        prefs.get_devices()
        for device in prefs.devices:
            device.use = device.type == "CUDA"
        scene.cycles.device = "GPU" if any(d.use for d in prefs.devices) else "CPU"
    except (AttributeError, TypeError, RuntimeError):
        scene.cycles.device = "CPU"
    scene.render.bake.use_selected_to_active = True
    extent = max((high.matrix_world @ Vector(c) - high.matrix_world.translation).length for c in high.bound_box)
    scene.render.bake.cage_extrusion = max(extent * 0.015, 0.001)
    scene.render.bake.max_ray_distance = max(extent * 0.08, 0.01)
    scene.render.bake.margin = 8
    high.select_set(True)
    low.select_set(True)

    def image(name, color=False):
        result = bpy.data.images.new(name, size, size, alpha=True)
        result.colorspace_settings.name = "sRGB" if color else "Non-Color"
        target.image = result
        nodes.active = target
        return result

    progress(35, "web_normal")
    normal = image("WebNormal")
    bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT")

    def bake_channel(socket, name, percent, color=False):
        progress(percent, "web_" + name)
        destination = image("Web" + name, color)
        restorations = []
        for source_material in set(high.data.materials):
            if not source_material or not source_material.use_nodes:
                continue
            tree = source_material.node_tree
            bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
            if bsdf is None or output is None:
                raise RuntimeError("Unsupported material for PBR baking.")
            previous = [link.from_socket for link in output.inputs["Surface"].links]
            emission = tree.nodes.new("ShaderNodeEmission")
            value = bsdf.inputs[socket]
            if value.is_linked:
                tree.links.new(value.links[0].from_socket, emission.inputs["Color"])
            else:
                v = value.default_value
                emission.inputs["Color"].default_value = v if color else (v, v, v, 1)
            tree.links.new(emission.outputs[0], output.inputs["Surface"])
            restorations.append((tree, output, emission, previous))
        try:
            bpy.ops.object.bake(type="EMIT")
        finally:
            for tree, output, emission, previous in restorations:
                tree.nodes.remove(emission)
                for socket_source in previous:
                    tree.links.new(socket_source, output.inputs["Surface"])
        return destination

    color = bake_channel("Base Color", "color", 50, True)
    rough = bake_channel("Roughness", "roughness", 65)
    metal = bake_channel("Metallic", "metallic", 75)
    progress(82, "web_materials")
    # Connect separate baked scalar maps; Blender's glTF exporter packs G/B into ORM.
    for baked, socket_name in [(color, "Base Color"), (rough, "Roughness"), (metal, "Metallic")]:
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = baked
        material.node_tree.links.new(texture.outputs["Color"], shader.inputs[socket_name])
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = normal
    mapping = nodes.new("ShaderNodeNormalMap")
    material.node_tree.links.new(texture.outputs["Color"], mapping.inputs["Color"])
    material.node_tree.links.new(mapping.outputs["Normal"], shader.inputs["Normal"])
    # Save/pack the bake results before the exporter reads their pixel data.
    for baked in [color, rough, metal, normal]:
        baked.filepath_raw = str(directory / (baked.name + ".png"))
        baked.file_format = "PNG"
        baked.save()
        baked.pack()
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    progress(90, "web_compress")
    bpy.ops.export_scene.gltf(filepath=str(directory / "web.glb"), export_format="GLB", use_selection=True,
                              export_image_format="WEBP", export_image_quality=90,
                              export_draco_mesh_compression_enable=True,
                              export_draco_mesh_compression_level=6,
                              export_tangents=True, export_animations=False)
    report = {"triangles": triangles, "source_triangles": original_triangles, "texture_size": size,
              "normal_map": True, "compression": "Draco + WebP", "bake_device": scene.cycles.device}
    (directory / "report.json").write_text(json.dumps(report))
    progress(98, "web_validate")


if __name__ == "__main__":
    main()
