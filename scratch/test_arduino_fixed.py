import os, sys, json, re
sys.path.insert(0, 'backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from core.models import Artifact
from core.image_design import _component_visuals

spec = json.loads(Artifact.objects.get(id=318).body)
arduino = next(c for c in spec['components'] if c['id'] == 'ctrl_arduino')

# Temporarily patch _component_visuals logic
lines = open('backend/core/image_design.py').readlines()
# let's see what happens when wing is fixed
