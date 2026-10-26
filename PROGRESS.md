# ForgeBot implementation progress

Updated: 2026-10-26

This document tracks what is in the repository against the architecture in [ARCHITECTURE.md](ARCHITECTURE.md). “Implemented” means present in the local codebase; it does not mean hardware-ready or safety-certified.

## Implemented

- React workbench and Django API with sign-in, projects, jobs, events, artifacts, review, and export foundations.
- Server-side Meta Model API configuration and safe connectivity check. The API key is read from `backend/.env` and is not sent to the browser.
- Constrained Meta-generated tabletop 4-DOF arm proposal and conceptual BOM, with human review before file generation.
- **New: Meta-generated research humanoid concept.** The user can choose a new platform class, review bounded dimensions, sensors, compute proposal, and an unverified BOM, then approve local generation of a 31-DOF body plus two 20-DOF hand chains. The output includes a primitive-geometry URDF, sensor frames, a non-hardware control interface, and a neutral-pose 3D assembly viewer.
- Local arm URDF and controller-scaffold generation plus semantic, URDF, and static-code validation.
- **New: URDF-driven 3D arm assembly.** The editor parses generated links, supported primitive visuals, visual origins, joint origins/axes, and URDF limits. Joint sliders now articulate downstream links from the URDF chain.
- **New: target-based arm kinematics and motion preview.** A four-axis inverse-kinematics solver uses the URDF link lengths and limits; the canvas shows target reachability and computed position error. A pick/place sequence animates joint-space motion with velocity-limited timing, gripper-finger motion, payload transfer, a path trace, and a sampled tabletop/work-area clearance gate.
- Local deterministic rover demo with route visualization and route validation.
- **New: Meta vision observation from an uploaded or manually captured still image.** The signed-in user explicitly starts analysis. The response includes a natural-language scene summary, object appearance/state, image-relative bounding regions, spatial relationships, confidence/uncertainty, visual graspability hypotheses, and cautions. The image and response are not stored by ForgeBot.
- **New: Image-to-robot concept workflow.** From a selected reference image, the user gives the desired robot task, approximate target height, and optional constraints. Meta proposes a bounded primitive component tree; a human reviews the design and assumptions; local code generates a URDF visual assembly, OpenSCAD source, BOM concept, and structural validation report. The image is held in memory for the model request and is not stored in ForgeBot.

## In progress / next milestones

| Order | Milestone | Acceptance evidence | Status |
| --- | --- | --- | --- |
| 1 | URDF-driven 3D assembly | Display every supported link and joint from the generated URDF; joint axes, origins, and limits match the file; pose controls move the corresponding downstream links. | Implemented for generated primitive-geometry arm URDFs; mesh visuals and general robot types are unsupported |
| 2 | Target-based kinematics | Forward kinematics agree with known test poses; inverse kinematics reaches reachable targets within 10 mm, respects limits, and reports unreachable targets. | Implemented for the generated 4-axis arm; analytic FK/IK and limit/reach feedback; acceptance fixtures still needed |
| 3 | Kinematic pick/place animation | Animate approach, close, lift, transfer, release, and retreat with velocity-limited interpolation; display the end-effector path; flag unsupported/colliding paths. | Implemented first pass: velocity-limited joint interpolation, gripper/payload visualization, path trace, and sampled link-centerline table/work-area clearance. Self-collision, object/bin collision, dynamics, and real grasp validation remain |
| 4 | Calibrated AI vision | Calibrate camera-to-table coordinates; retain model scene descriptions and object identity while deriving position from calibration/depth; report uncertainty and allow human correction. | Implemented first pass: click four known work-area corners, solve planar homography, map selected object's lower image-box center, edit XYZ, then pass reviewed target into arm planner. Depth, tracking, persistent calibration, and measured localization accuracy remain |
| 5 | New humanoid platform workflow | Create a humanoid job from a mission; review its typed proposal; generate a connected URDF with 31 body joints, 20 joints per hand, valid inertials/limits, sensors, and display its neutral pose. | Implemented as a kinematic concept path. Joint distribution/inertias are templates; no balance, gait, actuator sizing, parametric CAD, or physics validation. |
| 6 | Mountable component design | Parts declare geometry, mass, mounting frame/interface, and constraints; editor places them at declared frames and reports fit, mass, reach, and collision findings. | First slice implemented: local GLB/glTF/STL import, root/parent selection, named origin mount frames, translation/scale/units, approximate mesh-bounds overlap warnings, and JSON manifest export. Not persistent or server validated; no true CAD fit checks. |
| 7 | Image-to-robot CAD concept | Upload a reference image, state the robot task and a scale anchor, review a validated component tree, approve, visualize a generated robot description, and download editable CAD source. | Implemented first pass using primitive-only OpenSCAD and URDF output. It is image-inspired conceptual geometry, not recovered CAD or manufacturing-ready design. Live Meta request not exercised in this environment. |
| 8 | Physics simulation and demo acceptance | Isolated simulator; reproducible configuration/results; humanoid stand/walk/manipulation and tabletop sorting scenarios with measured collision and task outcomes. | Not started; select and integrate a physics runtime before claiming humanoid motion. |
| 9 | Reliable local operations | Durable queue, cancellation/retries, storage/retention, observability, exports, and documented local Docker deployment. | Partial; development flow currently uses local SQLite and an in-process model worker |

## CAD and custom-platform implementation plan

The chosen first integration is local file exchange, not a tightly coupled CAD plugin. No FreeCAD account is required: FreeCAD is a locally installed, open-source desktop application. Use FreeCAD for dimensioned mechanical parts and STEP exchange; use Blender when visual mesh editing is useful and export GLB/glTF for ForgeBot's viewer. ForgeBot should preserve the source file and record the originating application/version when known.

1. **Component asset contract:** versioned manifest for mesh references, units, physical properties and provenance, named mount frames, and assembly edges.
2. **Browser assembly editor:** load local GLB/glTF and STL meshes, place parts by named mount frames, inspect transforms and component tree, and export the manifest.
3. **Geometry checks:** validate frame references and units; show approximate bounds/intersections with clear warnings. Add STEP/kernel-based fit and tolerance checks only when a CAD kernel is integrated.
4. **Robot description bridge:** generate URDF visuals and joints from the reviewed assembly graph; preserve links to original CAD and mesh files.
5. **Simulation bridge:** import the validated robot description into the selected physics engine, configure material/contact properties and controllers, then record reproducible outcomes.

The first slice is present in the job workspace under **CAD assembly**. Its acceptance gate is two imported meshes connected by declared mount frames, a visible assembly and component tree, explicit unit/frame errors, coarse overlap findings, and a downloadable manifest. Remaining in this slice: mount-frame pose editing/alignment, persistence with the job, validation of cycles/missing frames/unit mismatches, and URDF export. It does not claim solid-CAD interference, tolerance, strength, or manufacturing checks.

## Image-to-robot concept workflow

In a project, select a reference image in the vision panel, enter the robot task and approximate overall height, optionally add constraints, then choose **Generate robot concept**. ForgeBot sends the selected image and text to the configured Meta Model API, validates a 3–12 component tree, and presents the proposal for human review. Approval generates deterministic primitive geometry as URDF and OpenSCAD source, then validates that the URDF forms a connected tree. The canvas visualizes the generated URDF and the CAD source can be downloaded and opened in OpenSCAD.

The image supplies appearance and rough proportions only. The provided height is a scale anchor, not a measured dimension. Component shapes are boxes, cylinders, or spheres; joints are fixed; mass, materials, actuators, fasteners, tolerances, internal mechanisms, and load capacity are not inferred. The generated output is a concept model, not production CAD. The reference image is not written to ForgeBot storage; it is sent to Meta when the user submits the design request. This first pass has not been exercised against a live provider response in the current build session.

## Vision behavior and limits

The current “eyes” feature calls the configured Meta model with one user-selected image. It returns a detailed structured observation instead of only coordinates. Bounding boxes are normalized image regions for visual grounding and overlays; the optional planar homography converts a selected region's lower-center to an approximate workcell target. This is not depth, object geometry, mass, safe grasping, or collision-free motion.

The first planar camera calibration is now present. It assumes the documented 0.8 x 0.6 m work surface and arm-base origin, uses four user-clicked table corners, maps the selected detection's lower box-center to XY, and allows XYZ edits before sending the target to the planner. It has not been measured against a physical calibration fixture. Next is calibration provenance/persistence, depth or range sensing where needed, object identity tracking across frames, and accuracy measurement. Do not connect vision output directly to hardware control.

## Current MVP success definition

ForgeBot can be called a successful local prototype when a user can create a tabletop-arm proposal, inspect and approve it, view and manipulate the URDF-derived robot model, capture a workcell image for model-backed scene understanding, calibrate the image to the configured table, review/edit a detected object's mapped target, and play a velocity-limited pick-and-place animation that passes the documented simplified tabletop clearance gate. Each stage should expose its inputs, assumptions, uncertainty, validation results, and failures. Full self/object collision checking, depth-based perception, physical operation, manufacturing-ready CAD, certified safety, and autonomous real-world manipulation are outside this current slice.

The measured localization and manipulation thresholds must be fixed for a recorded test setup before claiming the integrated demo passes. The current 10 mm kinematics target is a software acceptance goal for simulated/known poses, not a physical accuracy claim.

## Humanoid platform success checks

The first humanoid milestone is complete when a user can select **Research humanoid**, submit a mission, review the AI proposal and unverified BOM, approve it, and inspect a connected URDF in the 3D viewer. The generated description should expose 31 body joints, 20 articulated joints per hand, fixed sensor frames matching the approved sensor suite, finite positive inertial entries, and valid limits for every actuated joint. This confirms the design pipeline and kinematic assembly only. The next acceptance gate is importing the asset into an isolated physics simulator and demonstrating a stable stand, a short walk, and a simple two-arm manipulation task with recorded engine/version, timestep, environment, collisions, and repeatable outcomes.
