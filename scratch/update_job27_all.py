import os, sys, json
sys.path.insert(0, 'backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from core.models import Job, Artifact
from core.image_design import (
    ground_image_design,
    generate_cad_package,
    generate_image_robot_urdf,
    _component_visuals,
)
from core.freecad_export import run_freecad_export

job = Job.objects.get(id=27)

# 1. Update spec artifact
spec_art = Artifact.objects.filter(job=job, kind='spec').first()
spec = json.loads(spec_art.body)

grounded, report = ground_image_design(spec, 0.25)
spec_art.body = json.dumps(grounded, indent=2)
spec_art.save()
print("Saved grounded spec artifact.")

# Check arduino visuals in grounded spec
arduino = next(c for c in grounded['components'] if c['id'] == 'ctrl_arduino')
vis = _component_visuals(arduino)
print(f"ctrl_arduino visuals count: {len(vis)} (First color: {vis[0]['color']}, Role: {arduino['role']})")

# 2. Update URDF artifact (for the Robot Canvas tab!)
urdf_art = Artifact.objects.filter(job=job, kind='urdf').first()
if urdf_art:
    urdf_content = generate_image_robot_urdf(grounded)
    urdf_art.body = urdf_content
    urdf_art.save()
    print("Updated Job 27 URDF artifact for Robot Canvas.")

# 3. Update CAD package & run FreeCAD export
pkg = generate_cad_package(grounded)
cad_art = Artifact.objects.filter(job=job, kind='cad').first()
if cad_art:
    cad_art.body = json.dumps(pkg, indent=2)
    cad_art.save()
    print("Updated Job 27 CAD package artifact.")

print("Running headless FreeCAD export...")
freecad_res = run_freecad_export(27, pkg)
print("FreeCAD export status:", freecad_res.get("status"))
print("FreeCAD preview meshes count:", len(freecad_res.get("preview_meshes", {})))

cad_exp_art = Artifact.objects.filter(job=job, kind='cad_export').first()
if cad_exp_art:
    cad_exp_art.body = json.dumps(freecad_res, indent=2)
    cad_exp_art.save()
    print("Updated Job 27 cad_export artifact with completed status and all preview meshes.")
