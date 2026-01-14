# Flexxbotics Transformers  
**Industrial Device & Automation Transformers for the FlexxCore Platform**

**Flexxbotics Transformers** are open-source, industrial-grade connector drivers designed for use with **FlexxCore**, the Flexxbotics Smart Factory and Workcell Automation platform.

These transformers provide a standardized abstraction layer for integrating **industrial machines, robots, PLCs, CNCs, CMMs, and automation equipment** using both **open industrial protocols** and **vendor-specific proprietary interfaces**.

> This repository is focused on **industrial automation transformers** — not machine learning or NLP transformers.

---

## Overview

Flexxbotics Transformers enable **many-to-many interoperability** across heterogeneous industrial devices within a single automation runtime.

Key concepts:

- Transformers are loaded into the **Flexxbotics runtime** (Edge or Standalone)
- Each transformer exposes a standardized interface regardless of underlying protocol
- New transformers automatically interoperate with all others loaded in the same runtime
- Designed for **workcell-level automation**, orchestration, and recovery
- Implemented in **Python**, using familiar development workflows

---

## What You’ll Find in This Repository

This repository includes production-ready examples and templates for building industrial automation integrations:

- **Open-source equipment transformers** compatible with FlexxCore
- A base **Transformer Template** for creating new device connectors
- A reference **Workcell Transformer** that composes multiple transformers to:
  - Reconcile overall workcell state  
  - Perform automated fault recovery and restarts  
  - Execute threaded and asynchronous controls  
  - Coordinate multi-device automation logic  
- Example **Automation Scripts** that:
  - Are callable from the Flexxbotics HMI / Controls Configurator  
  - Can run in real time  
  - Interact directly with transformers to execute automation tasks  

---

## What You Can Do With Transformers

Using Flexxbotics Transformers, you can:

- Connect machines, robots, and inspection equipment together
- Build multi-machine robotic automation cells
- Abstract vendor-specific protocols behind a common interface
- Create custom machine-to-automation logic in Python
- Extend or modify existing transformers to meet site-specific needs
- Redistribute transformers under a permissive open-source license

---

## Intended Use Cases

Flexxbotics Transformers are commonly used for:

- Robotic workcells  
- CNC machine integration  
- PLC-based automation  
- Industrial inspection and metrology  
- Smart factory data acquisition  
- Closed-loop machine-to-robot coordination  

---

## License

All transformers in this repository are released under the **Apache 2.0 License**.

- Commercial use is permitted  
- Modifications are not required to be contributed back  
- Contributions are welcome and encouraged  

See the `LICENSE` file for full details.

---

## Feedback & Community

We welcome feedback from integrators, automation engineers, and developers.

- Share what works well
- Call out pain points or limitations
- Propose new transformer ideas or enhancements

Your input directly influences the evolution of the Flexxbotics Transformers ecosystem.

---

## Tags / Keywords

`industrial automation` · `robotics` · `workcell automation` · `CNC` · `PLC` · `CMM` ·  
`industrial protocols` · `factory automation` · `smart factory` · `robot integration` ·  
`FlexxCore` · `Flexxbotics` · `Flexxbotics Transformers` · `Python automation`
