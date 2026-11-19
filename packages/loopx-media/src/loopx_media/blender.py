"""Optional Blender 4.5 computation behind content-ops' admitted scene port.

Only this subprocess imports bpy. Input is bounded declarative geometry, never
user Python, an uploaded blend, an add-on or a filesystem discovery request.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import zipfile


def available() -> bool:
    return importlib.util.find_spec("bpy") is not None


def _call(mode: str, request: Path, directory: Path, check) -> None:
    # File-backed logs avoid a full stderr pipe blocking a real Blender render.
    with (directory.parent / f"{directory.name}-{mode}.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                mode,
                str(request),
                str(directory),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            while process.poll() is None:
                check()
                time.sleep(0.1)
            if process.returncode:
                raise ValueError("Blender could not complete the declared scene")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def render_shot(shot: dict, plan: dict, out: Path, check) -> tuple[Path, dict]:
    if not available():
        raise ValueError(
            "Install the optional Blender extra before rendering this scene"
        )
    parent = out / "blender"
    parent.mkdir(exist_ok=True)
    directory = parent / shot["id"]
    request = parent / f"{shot['id']}.json"
    payload = {
        "scene": shot["scene"],
        "width": plan["width"],
        "height": plan["height"],
        "fps": plan["fps"],
        "frames": round(shot["seconds"] * plan["fps"]),
    }
    request.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _call("render", request, directory, check)
    _call("verify", request, directory, check)
    qa = json.loads((directory / "qa.json").read_text(encoding="utf-8"))
    from PIL import Image

    expected = [f"frame-{i:04}.png" for i in range(1, payload["frames"] + 1)]
    for name in ("rgba", "depth"):
        paths = sorted((directory / name).glob("*.png"))
        if [path.name for path in paths] != expected:
            raise ValueError("Blender did not render every declared pass frame")
        for path in paths:
            check()
            with Image.open(path) as image:
                image.load()
                if image.size != (plan["width"], plan["height"]):
                    raise ValueError(
                        "Blender pass dimensions differ from the admitted scene"
                    )
                if name == "rgba" and (
                    image.mode != "RGBA" or not image.getchannel("A").getbbox()
                ):
                    raise ValueError(
                        "The Blender scene contains no visible RGBA geometry"
                    )
                if name == "depth" and (
                    image.mode not in {"I", "I;16"}
                    or image.getextrema()[0] == image.getextrema()[1]
                ):
                    raise ValueError("The Blender depth pass contains no scene range")
    return directory / "rgba", {
        "id": shot["id"],
        "frames": payload["frames"],
        "rgba_frames": len(expected),
        "depth_frames": len(expected),
        **qa,
    }


def project_archive(plan: dict, out: Path) -> None:
    root = out / "blender"
    if not root.is_dir():
        return
    # Replay uses a fresh destination and the same builder, not embedded blend scripts.
    (root / "scene-builder.py").write_bytes(Path(__file__).read_bytes())
    (root / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (root / "README.txt").write_text(
        "Private illustrative Blender project. No publication approval.\n"
        "Requires Python3.11 and bpy4.5 LTS. For each SHOT.json:\n"
        "python scene-builder.py render SHOT.json NEW_DIRECTORY\n"
        "python scene-builder.py verify SHOT.json NEW_DIRECTORY\n"
        "Each scene.blend can be opened with scripts disabled. Paths are relative.\n"
        "rgba/ contains straight RGBA PNG frames. depth/ contains a 16-bit Z pass\n"
        "mapped from distance0..50 to0..65535, with values beyond50 clamped.\n"
        "CPU Cycles8samples; quality and comprehension still require review.\n",
        encoding="utf-8",
    )
    paths = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix != ".log"
    ]
    if sum(path.stat().st_size for path in paths) > 128 * 1024 * 1024:
        raise ValueError("The Blender project exceeds the bounded output budget")
    with zipfile.ZipFile(
        out / "blender-project.zip", "x", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for path in paths:
            archive.write(path, path.relative_to(root).as_posix())


def _blender():
    import bpy

    if bpy.app.version[:2] != (4, 5):
        raise ValueError("This provider supports the Blender4.5 LTS API")
    return bpy


def _material(bpy, name, color, metallic=0.15, roughness=0.4):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    node = material.node_tree.nodes["Principled BSDF"]
    node.inputs["Base Color"].default_value = (*color, 1)
    node.inputs["Metallic"].default_value = metallic
    node.inputs["Roughness"].default_value = roughness
    return material


def _render(request: dict, directory: Path) -> None:
    bpy = _blender()
    from mathutils import Vector

    directory.mkdir(mode=0o700, exist_ok=False)
    for name in ("rgba", "depth"):
        (directory / name).mkdir(mode=0o700)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.use_scripts_auto_execute = False
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.cycles.use_denoising = True
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.resolution_x, scene.render.resolution_y = (
        request["width"],
        request["height"],
    )
    scene.render.resolution_percentage = 100
    scene.render.fps = request["fps"]
    scene.frame_start, scene.frame_end = 1, request["frames"]
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "Standard"
    scene.world = bpy.data.worlds.new("Neutral studio")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.35
    specification = request["scene"]
    for item in specification["objects"]:
        if item["shape"] == "box":
            bpy.ops.mesh.primitive_cube_add(size=1, location=item["position"])
        elif item["shape"] == "sphere":
            bpy.ops.mesh.primitive_uv_sphere_add(
                segments=24, ring_count=12, radius=0.5, location=item["position"]
            )
        else:
            raise ValueError("Unsupported geometry")
        ob = bpy.context.object
        ob.name = item["id"]
        ob.scale = item["scale"]
        ob.rotation_euler = [math.radians(value) for value in item["rotation_degrees"]]
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        ob.data.materials.append(
            _material(
                bpy, item["id"], item["color"], item["metallic"], item["roughness"]
            )
        )
        if item["shape"] == "box":
            bevel = ob.modifiers.new("Soft edge", "BEVEL")
            bevel.width, bevel.segments = 0.06, 3
        else:
            for face in ob.data.polygons:
                face.use_smooth = True
        for key in item["keyframes"]:
            ob.location = key["position"]
            ob.keyframe_insert(data_path="location", frame=key["frame"])
    for index, link in enumerate(specification["connections"]):
        a = Vector(
            next(
                item["position"]
                for item in specification["objects"]
                if item["id"] == link["from"]
            )
        )
        b = Vector(
            next(
                item["position"]
                for item in specification["objects"]
                if item["id"] == link["to"]
            )
        )
        curve = bpy.data.curves.new(f"Connection-{index}", "CURVE")
        curve.dimensions, curve.bevel_depth, curve.bevel_resolution = "3D", 0.025, 3
        spline = curve.splines.new("POLY")
        spline.points.add(32)
        for i, point in enumerate(spline.points):
            t = i / 32
            point.co = (
                *(a.lerp(b, t) + Vector((0, 0, 0.65 * math.sin(math.pi * t)))),
                1,
            )
        ob = bpy.data.objects.new(f"Connection-{index}", curve)
        scene.collection.objects.link(ob)
        curve.materials.append(_material(bpy, f"Route-{index}", link["color"]))
    camera = specification["camera"]
    bpy.ops.object.camera_add()
    scene.camera = bpy.context.object
    scene.camera.name = "Scene camera"
    scene.camera.data.type, scene.camera.data.ortho_scale = (
        "ORTHO",
        camera["ortho_scale"],
    )
    scene.camera.data.clip_end = 50
    for frame, position in (
        (1, camera["position"]),
        (request["frames"], camera["end_position"]),
    ):
        scene.camera.location = position
        scene.camera.rotation_euler = (
            (Vector(camera["target"]) - scene.camera.location)
            .to_track_quat("-Z", "Y")
            .to_euler()
        )
        scene.camera.keyframe_insert(data_path="location", frame=frame)
        scene.camera.keyframe_insert(data_path="rotation_euler", frame=frame)
    for item in specification["lights"]:
        bpy.ops.object.light_add(type="AREA", location=item["position"])
        lamp = bpy.context.object
        lamp.data.energy, lamp.data.size = item["energy"], item["size"]
        lamp.rotation_euler = (
            (Vector(camera["target"]) - lamp.location)
            .to_track_quat("-Z", "Y")
            .to_euler()
        )
    scene.view_layers[0].use_pass_z = True
    scene.use_nodes = True
    nodes, links = scene.node_tree.nodes, scene.node_tree.links
    nodes.clear()
    layers = nodes.new("CompositorNodeRLayers")
    links.new(
        layers.outputs["Image"], nodes.new("CompositorNodeComposite").inputs["Image"]
    )
    depth = nodes.new("CompositorNodeMapRange")
    depth.inputs["From Min"].default_value, depth.inputs["From Max"].default_value = (
        0,
        50,
    )
    depth.use_clamp = True
    links.new(layers.outputs["Depth"], depth.inputs["Value"])
    output = nodes.new("CompositorNodeOutputFile")
    output.save_as_render = False
    output.file_slots[0].save_as_render = False
    output.file_slots[0].use_node_format = True
    output.base_path, output.file_slots[0].path = "//depth", "frame-"
    output.format.file_format, output.format.color_mode, output.format.color_depth = (
        "PNG",
        "BW",
        "16",
    )
    links.new(depth.outputs["Value"], output.inputs[0])
    scene.render.filepath = "//rgba/frame-"
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(directory / "scene.blend"), compress=True)
    bpy.ops.render.render(animation=True)


def _verify(request: dict, directory: Path) -> None:
    bpy = _blender()
    bpy.ops.wm.open_mainfile(
        filepath=str(directory / "scene.blend"), load_ui=False, use_scripts=False
    )
    scene = bpy.context.scene
    if (
        scene.render.engine != "CYCLES"
        or scene.frame_end != request["frames"]
        or scene.frame_start != 1
        or scene.render.resolution_x != request["width"]
        or scene.render.resolution_y != request["height"]
        or scene.render.fps != request["fps"]
        or not scene.render.film_transparent
        or not scene.view_layers[0].use_pass_z
    ):
        raise ValueError("Saved Blender project differs from the scene")
    for item in request["scene"]["objects"]:
        ob = scene.objects.get(item["id"])
        if ob is None or ob.type != "MESH" or not ob.data.materials:
            raise ValueError("Saved Blender geometry is missing")
        for key in item["keyframes"]:
            scene.frame_set(key["frame"])
            if any(abs(a - b) > 1e-4 for a, b in zip(ob.location, key["position"])):
                raise ValueError("Saved animation differs from declared keyframes")
    for frame, field in ((1, "position"), (request["frames"], "end_position")):
        scene.frame_set(frame)
        if any(
            abs(a - b) > 1e-4
            for a, b in zip(scene.camera.location, request["scene"]["camera"][field])
        ):
            raise ValueError("Saved camera animation differs")
    (directory / "qa.json").write_text(
        json.dumps(
            {
                "project_reopened": True,
                "engine": scene.render.engine,
                "blender_version": bpy.app.version_string,
            }
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    mode, source, destination = sys.argv[1:]
    if mode not in {"render", "verify"}:
        raise ValueError("Unsupported Blender worker operation")
    payload = json.loads(Path(source).read_text(encoding="utf-8"))
    (_render if mode == "render" else _verify)(payload, Path(destination).resolve())
