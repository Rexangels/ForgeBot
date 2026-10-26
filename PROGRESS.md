# ForgeBot implementation progress

Updated: 2026-10-26

This document tracks what is in the repository against the architecture in [ARCHITECTURE.md](ARCHITECTURE.md). “Implemented” means present in the local codebase; it does not mean hardware-ready or safety-certified.

## Implemented

- React workbench and Django API with sign-in, projects, jobs, events, artifacts, review, and export foundations.
- Server-side Meta Model API configuration and safe connectivity check. The API key is read from `backend/.env` and is not sent to the browser.
- Constrained Meta-generated tabletop 4-DOF arm proposal and conceptual BOM, with human review before file generation.
- Local arm URDF and controller-scaffold generation plus semantic, URDF, and static-code validation.
- **New: URDF-driven 3D arm assembly.** The editor parses generated links, supported primitive visuals, visual origins, joint origins/axes, and URDF limits. Joint sliders now articulate downstream links from the URDF chain.
- **New: target-based arm kinematics and motion preview.** A four-axis inverse-kinematics solver uses the URDF link lengths and limits; the canvas shows target reachability and computed position error. A pick/place sequence animates joint-space motion with velocity-limited timing, gripper-finger motion, payload transfer, a path trace, and a sampled tabletop/work-area clearance gate.
- Local deterministic rover demo with route visualization and route validation.
- **New: Meta vision observation from an uploaded or manually captured still image.** The signed-in user explicitly starts analysis. The response includes a natural-language scene summary, object appearance/state, image-relative bounding regions, spatial relationships, confidence/uncertainty, visual graspability hypotheses, and cautions. The image and response are not stored by ForgeBot.

## In progress / next milestones

| Order | Milestone | Acceptance evidence | Status |
| --- | --- | --- | --- |
| 1 | URDF-driven 3D assembly | Display every supported link and joint from the generated URDF; joint axes, origins, and limits match the file; pose controls move the corresponding downstream links. | Implemented for generated primitive-geometry arm URDFs; mesh visuals and general robot types are unsupported |
| 2 | Target-based kinematics | Forward kinematics agree with known test poses; inverse kinematics reaches reachable targets within 10 mm, respects limits, and reports unreachable targets. | Implemented for the generated 4-axis arm; analytic FK/IK and limit/reach feedback; acceptance fixtures still needed |
| 3 | Kinematic pick/place animation | Animate approach, close, lift, transfer, release, and retreat with velocity-limited interpolation; display the end-effector path; flag unsupported/colliding paths. | Implemented first pass: velocity-limited joint interpolation, gripper/payload visualization, path trace, and sampled link-centerline table/work-area clearance. Self-collision, object/bin collision, dynamics, and real grasp validation remain |
| 4 | Calibrated AI vision | Calibrate camera-to-table coordinates; retain model scene descriptions and object identity while deriving position from calibration/depth; report uncertainty and allow human correction. | Implemented first pass: click four known work-area corners, solve planar homography, map selected object's lower image-box center, edit XYZ, then pass reviewed target into arm planner. Depth, tracking, persistent calibration, and measured localization accuracy remain |
| 5 | Mountable component design | Parts declare geometry, mass, mounting frame/interface, and constraints; editor places them at declared frames and reports fit, mass, reach, and collision findings. | Not started |
| 6 | Physics simulation and demo acceptance | Isolated simulator; reproducible configuration/results; complete tabletop sorting scenarios with measured localization, endpoint, collision, and task-completion outcomes. | Not started |
| 7 | Reliable local operations | Durable queue, cancellation/retries, storage/retention, observability, exports, and documented local Docker deployment. | Partial; development flow currently uses local SQLite and an in-process arm worker |

## Vision behavior and limits

The current “eyes” feature calls the configured Meta model with one user-selected image. It returns a detailed structured observation instead of only coordinates. Bounding boxes are normalized image regions for visual grounding and overlays; the optional planar homography converts a selected region's lower-center to an approximate workcell target. This is not depth, object geometry, mass, safe grasping, or collision-free motion.

The first planar camera calibration is now present. It assumes the documented 0.8 x 0.6 m work surface and arm-base origin, uses four user-clicked table corners, maps the selected detection's lower box-center to XY, and allows XYZ edits before sending the target to the planner. It has not been measured against a physical calibration fixture. Next is calibration provenance/persistence, depth or range sensing where needed, object identity tracking across frames, and accuracy measurement. Do not connect vision output directly to hardware control.

## Current MVP success definition

ForgeBot can be called a successful local prototype when a user can create a tabletop-arm proposal, inspect and approve it, view and manipulate the URDF-derived robot model, capture a workcell image for model-backed scene understanding, calibrate the image to the configured table, review/edit a detected object's mapped target, and play a velocity-limited pick-and-place animation that passes the documented simplified tabletop clearance gate. Each stage should expose its inputs, assumptions, uncertainty, validation results, and failures. Full self/object collision checking, depth-based perception, physical operation, manufacturing-ready CAD, certified safety, and autonomous real-world manipulation are outside this current slice.

The measured localization and manipulation thresholds must be fixed for a recorded test setup before claiming the integrated demo passes. The current 10 mm kinematics target is a software acceptance goal for simulated/known poses, not a physical accuracy claim.
