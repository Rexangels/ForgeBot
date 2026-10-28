# ForgeBot: System Architecture, Engineering Milestones & Roadmap

> **Executive Pitch Summary**: ForgeBot is a closed-loop **Vision-to-Hardware-and-Firmware Co-Design Studio**. Given a single reference image or mission prompt, ForgeBot uses Meta's multimodal model (`muse-spark-1.3`) to propose a structured robot specification, compiles a manufacturable multi-body **FreeCAD** assembly (`.FCStd`, `.STEP`, `.STL`, `.URDF`) with aligned M3 fastener patterns, trains an obstacle-avoidance policy in a **3D WebGL Obstacle Arena**, sizes the **3-hour battery & powertrain** from tire load and turning scrub, synthesizes an inspectable **Custom Carrier PCB (`.kicad_pcb`) & System Wiring Harness**, and packages a **deployable Vision + Sonar + Learned Controller firmware bundle (`.zip`)**.

---

## 1. End-to-End System Architecture Diagram

```mermaid
flowchart TD
    subgraph InputLayer["1. Multimodal Perception & Specification"]
        IMG["Reference Image / Mission Prompt"]
        VISION["Meta Vision Model (muse-spark-1.3)\nbackend/core/meta_client.py"]
        HOMO["Depth-Aware Homography & 3D Target Mapper\nbackend/core/vision.py"]
        GROUND["Spatial Grounding & Topology Validator\nbackend/core/image_design.py"]
        IMG --> VISION --> GROUND
        IMG --> HOMO
    end

    subgraph MechanicalCAD["2. Parametric Solid CAD & COTS Hardware Engine"]
        COTS["COTS Footprint Library (N20, TT, HC-SR04, RPi, M3)\nbackend/core/hardware.py"]
        HOLES["Cross-Part Coaxial Mating Hole Projector\nproject_assembly_mating_holes()"]
        SCAD["OpenSCAD + URDF Inertial/Collision Generator\ngenerate_cad_package() / generate_image_robot_urdf()"]
        FC["Headless FreeCAD OpenCASCADE Kernel\nbackend/core/freecad_export.py"]
        GROUND --> COTS --> HOLES --> SCAD --> FC
    end

    subgraph SimAndTraining["3. 3D Obstacle World Training & Co-Design Loop"]
        MASS["3D Mass, Inertia & Static Tip-Over Analyzer\nsimulate_image_robot()"]
        WORLD["8-Episode 3D Obstacle Policy Trainer\nbackend/core/world_training.py"]
        CODESIGN["Structural (CAD) & Software (Gain) Refinements\ngenerate_codesign_refinements()"]
        SCAD --> MASS --> WORLD --> CODESIGN
        CODESIGN -- "One-Click Apply & Rebuild" --> HOLES
    end

    subgraph MechatronicsPCB["4. Mechatronics, 3-Hour Battery, KiCad PCB & Firmware"]
        POWER["Recursive Tire-Load & 3h Battery Sizer\nbackend/core/mechatronics.py"]
        PCB["Custom Carrier PCB (.kicad_pcb / .kicad_sch)\n+ System Wiring Harness & ERC Checks"]
        FW["Deployable Vision + Sonar + Learned Policy Bundle\nforgebot-firmware-and-pcb.zip"]
        WORLD --> POWER --> PCB --> FW
        POWER -- "Sync Battery Envelope to CAD" --> HOLES
    end
```

---

## 2. Closed-Loop Co-Design Sequence Diagram

```mermaid
sequenceDiagram
    actor User as Engineer / Builder
    participant UI as React Studio (App.jsx / RoverView.jsx / MechatronicsPanel.jsx)
    participant API as Django REST API (views.py / demo.py)
    participant Meta as Meta Vision API (muse-spark-1.3)
    participant CAD as FreeCAD & Hardware Engine (hardware.py / freecad_export.py)
    participant Sim as Obstacle Trainer & Mechatronics (world_training.py / mechatronics.py)

    User->>UI: Upload robot reference photo + mission prompt
    UI->>API: POST /api/v1/projects/<id>/jobs
    API->>Meta: Two-stage visual inventory + 3D layout synthesis
    Meta-->>API: Grounded 16-part component tree (image-robot-concept/v1)
    User->>UI: Approve specification
    UI->>API: POST /api/v1/jobs/<id>/approve
    API->>CAD: Match COTS footprints + project M3 mating holes + build .FCStd / .STEP / .STL
    API->>Sim: Run 8-episode 3D obstacle training + 3-hour battery sizing + KiCad PCB synthesis
    Sim-->>UI: Render 3D WebGL obstacle arena, PCB layout, wiring harness & co-design cards
    User->>UI: Click "Apply Structural Refinement" or "Sync Battery Envelope to FreeCAD"
    UI->>API: POST /api/v1/jobs/<id>/edit-cad-sketches
    API->>CAD: Update component dimensions/poses & rebuild FreeCAD .FCStd / .STEP
    API->>Sim: Re-run 3D obstacle world training, battery sizing & firmware generation
    Sim-->>UI: Live-updated 3D assembly, trajectory clearance & firmware bundle
```

---

## 3. Completed Engineering Milestones

1. **Vision Depth & Spatial Generalization (`backend/core/vision.py`, `backend/core/meta_client.py`)**:
   - Height-aware planar homography (`project_pixel_at_height`) + two-view ray triangulation (`triangulate_two_view_ray_midpoint`).
   - Meta `muse-spark-1.3` two-stage visual inventory and 3D layout grounding with automatic transient error retry.
2. **Manufacturing-Ready CAD & COTS Hardware Detailing (`backend/core/hardware.py`, `backend/core/image_design.py`, `backend/core/freecad_export.py`)**:
   - Standard COTS footprint library (`N20`, `TT`, `HC-SR04`, `L298N`, `Raspberry Pi 4B`, `Arduino Uno`, `SG90/MG996R`, `3S LiPo`, `M3` standoffs, `65 mm` wheels).
   - Cross-part coaxial M3 mounting hole projection across mating plates and standoffs.
   - Multi-body solid booleans in FreeCAD (`.FCStd`, `.STEP`, `.STL`) + physics-ready `.URDF` with `<inertial>`, `<collision>`, and wheel `<joint type="revolute">`.
3. **3D Obstacle World Policy Training (`MuJoCo 3.17.0`) & Hardware/Software Co-Design (`backend/core/world_training.py`, `frontend/src/RoverView.jsx`)**:
   - Native **MuJoCo `3.17.0` (`MJCF`)** 3D rigid-body contact and sprung roll/pitch suspension simulation across 8 training episodes (`sim/forgebot_rover_mjcf.xml`).
   - Interactive Three.js 3D WebGL arena with 7 obstacles, spinning wheels, 3D sprung chassis roll/pitch dynamics, 3D routed electrical wiring harness (`⚡ 3D Wiring: ON/OFF`), ultrasonic cones, and Episode 1 (colliding) vs. Episode 4 (converged, 0 collisions) trajectory replay.
   - Automated Structural CAD Refinement cards (with one-click FreeCAD rebuild & re-simulation) + Learned Policy Gain table.
4. **Mechatronics, 3-Hour Battery Sizer, 3D FreeCAD Wire Harness, Inspectable KiCad PCB & Vision Firmware (`backend/core/mechatronics.py`, `frontend/src/MechatronicsPanel.jsx`)**:
   - Recursive tire-load & 3-hour battery endurance solver coupling CAD dry mass, tire rolling resistance, turning scrub, and `7.50 W` vision/avionics load.
   - **3D Routed Electrical Wire Harness (`parts/wiring_harness.stl`) & Detailed 3D PCB Solids** (XT60, 10A blade fuse, 5V 3A buck converter + toroidal inductor, 40-pin GPIO header, JST-XH headers, and raised copper power traces) built directly into `.FCStd`, `.STEP`, `.STL`, and the 3D WebGL viewers.
   - Interactive 2D Custom Carrier PCB inspector (`F.Cu`, `B.Cu`, `4× M3` standoff-aligned mounting holes) + native `.kicad_pcb` & `.kicad_sch` export + 5 automated `ERC/DRC` checks.
   - Downloadable firmware `.zip` (`hardware_pinout.py`, `vision_obstacle_detector.py`, `obstacle_avoidance_controller.py`, `run_robot.py --sim-self-test`, `pcb/forgebot_carrier.kicad_pcb`, `sim/forgebot_rover_mjcf.xml`).

---

## 4. Prioritized Future Roadmap (Where to Pick Up Next)

- **P1 — One-Click Gerber, Drill & JLCPCB Pick-and-Place (`CPL` / `BOM`) Export**: Add `kicad-cli` or native Gerber RS-274X + Excellon drill + JLCPCB centroid `.csv` generation so `forgebot_carrier.kicad_pcb` can be ordered with components pre-soldered.
- **P2 — Counterbores, Nut Traps & Heat-Set Insert Bosses in FreeCAD**: Extend `project_assembly_mating_holes` with 3D-printable M3 brass heat-set insert bosses and DIN 912 socket head counterbores.
- **P3 — ROS 2 (`humble`/`jazzy`) Launch & Navigation Package Export**: Bundle a complete `forgebot_bringup` ROS 2 package alongside the standalone Python runtime in `forgebot-firmware-and-pcb.zip`.

