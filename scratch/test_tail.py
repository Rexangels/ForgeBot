import sys
import os
sys.path.insert(0, '.')
sys.path.insert(0, 'backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
import core.world_training as wt
from scratch.test_layout import params, waypoints, obstacles, robot_radius_m

ep = wt._simulate_policy_episode(
    episode_idx=1,
    params=params,
    waypoints=waypoints,
    obstacles=obstacles,
    robot_radius_m=robot_radius_m,
    com_height_m=0.12,
    stability_margin_m=0.15,
    wheel_radius_m=0.04,
    track_width_m=0.47,
)

print(f"Total points: {len(ep['trajectory'])}")
for pt in ep['trajectory'][-15:]:
    print(f"t={pt['t']:.2f} x={pt['x']:.2f} y={pt['y']:.2f} heading={pt['heading']:.2f} clear={pt['clearance_m']:.3f}")
