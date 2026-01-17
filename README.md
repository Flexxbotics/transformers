# Flexxbotics Transformers
**Industrial Device & Workcell Transformers for the FlexxCore Platform**

![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Language](https://img.shields.io/badge/language-Python-blue)
![Domain](https://img.shields.io/badge/domain-Industrial%20Automation-informational)
![Platform](https://img.shields.io/badge/platform-FlexxCore-success)

---

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)"
            srcset="Documentation/Flexxbotics_Logo.png">
    <source media="(prefers-color-scheme: light)"
            srcset="Documentation/Flexxbotics_Logo-White.jpg">
    <img src="Documentation/Flexxbotics_Logo-White.jpg"
         alt="Flexxbotics Logo"
         style="width: 100%; max-width: 800px;" />
  </picture>
</p>

## Overview

This repository is home to **open-source industrial transformers** compatible with **FlexxCore**, the runtime platform at the core of the **Flexxbotics Smart Factory and Workcell Automation system**.

In FlexxCore architecture, a **Transformer** is the software component responsible for **communicating with equipment**. Transformer methods become the runtime execution points for:

- Status reads  
- Variable reads and writes  
- Commands  
- Polling logic  
- Automation behavior  

> This repository contains **industrial automation transformers** — not machine learning or NLP transformers.

Flexxbotics Transformers enable **many-to-many interoperability** across heterogeneous industrial equipment, allowing robots, CNCs, PLCs, inspection systems, and workcells to operate together through a common runtime and API.

---

## Links
- [Transformers Wiki & Flexxbotics Developer Guide](../../wiki).
- Download Software Runtime and Studio: https://flexxbotics.com/download/
- Technical Documentation (Install Guide, Developer Guide): https://flexxbotics.com/technical-documents/

---

## What You Can Do

- Connect machines to robots and other automation
- Create multi-machine automation cells
- Program your own machine-to-automation transformers
- Get, use, modify, and redistribute transformers

---

## What You’ll Find in This Repository

- Examples of base **Transformer Templates** for creating your own transformer
- Examples of **Workcell Transformers**, which embed multiple transformers to:
  - Reconcile workcell state  
  - Perform automatic restarts  
  - Run threaded controls  
  - Provide many additional advanced capabilities
- Examples of **Automation Scripts** callable from the Flexxbotics HMI Controls Configurator or run in realtime that can communicate with transformers to perform automation tasks.
- **Open source equipment transformers** for use with Flexxbotics software:
  
| Name | Description | Directory |
|------|------------|------|
| OPC UA | Protocol definition for communication with OPC-UA servers | Protocols/opcua.py |
| MTConnect | Protocol definition for communication with MTConnect agents | Protocols/mtconnect.py |
| MQTT | Protocol definition for communication with MQTT brokers | Protocols/mqtt.py |
| TCP/IP | Protocol definition for TCP/IP socket communication | Protocols/tcp.py |
| Modbus-TCP | Protocol definition for communication with Modbus-TCP enabled devices | Protocols/modbus.py |
| Siemens S7comm | Protocol definition for communication with S7comm based Siemens PLCs | Protocols/siemens_s7comm.py |
| Allen Bradley EIP-Logix | Protocol definition for communication with EIP-Logix based Allen Bradley PLCs | Protocols/allen_bradley_eip-logix.py |
| Beckhoff ADS Twincat | Protocol definition for communication with ADS Twincat based Beckhoff PLCs | Protocols/beckhoff_ads_twincat.py |
| Serial | Protocol definition for communication with serial devices | Protocols/serial.py |
| Universal Robots | Connector driver for all models of the Universal Robot 6-axis robotic arms | Transformers/Robots/UniversalRobots |
| FANUC Robots | Connector driver for all models of FANUC 6-axis robotic arms (industrial & collaborative) | Transformers/Robots/Fanuc |
| Haas Next Gen (NGC) | Connector driver for all models of Haas machines using the Next Gen Controller | Transformers/CNCs/Haas |
| Haas Serial | Connector driver for all models of Haas machines using the legacy controller | Transformers/CNCs/Haas |
| Fanuc FOCAS2 | Connector driver for all machines using I-Series FANUC-based controllers | Transformers/CNCs/Fanuc CNC |
| Heidenhain TNC | Connector driver for TNCRemo support for Heidenhain TNC-based controllers | Transformers/CNCs/Heidenhain |
| Yaskawa | Connector driver for Modbus-TCP enabled communication with Yaskawa MP2600 motor controllers | yaskawa_mp2600.py |
| Okuma | Connector driver for Okuma OSP300 and later controllers | Transformers/CNCs/Okuma |
| Hexagon | Connector driver for Hexagon CMMs | Transformers/CMMs/Hexagon |
| Keyence Telecentric | Connector driver for Keyence telecentric measurement devices | Transformers/Inspection/KeyenceTelecentric |
| Keyence Profiler | Connector driver for Keyence profile sensors | Transformers/Inspection/KeyenceProfiler |
| COGNEX | Connector driver for Modbus-TCP enabled communication with Cognex In-Sight cameras | Transformers/Cameras/Cognex |
| TRUMPF Laser Marker | Connector driver for Trumpf laser markers | Transformers/Lasers/Trumpf |
| FOBA | Connector driver for the M-Series FOBA lasers with Mark-US TCP interface | Transformers/Lasers/Foba |
| SICK | Connector driver for the SICK FlexiCompact safety PLC | Transformers/PLCs/FlexiCompact |
| WAGO | Connector driver for Modbus-TCP enabled communication with Wago I/O blocks | Transformers/IO/Wago |
| Sturtevant Richmont | Connector driver for the Sturtevant Richmont Global400 torque controller interface | Transformers/AssemblyTools/Global400 |


---

## Key Characteristics

- Transformers are **loaded into the Flexxbotics runtime** to operate
- Each transformer abstracts vendor-specific protocols behind a standardized interface
- Every transformer added to a runtime is **automatically compatible with all others**
- Transformers are implemented in **Python**
- You can use **any Python IDE** to develop, extend, or debug transformers

---

## FlexxCore Concepts

FlexxCore is built around **Devices** and **Transformers**, with supporting concepts such as **Machine Models**, **Protocols**, **Extensions**, **Adapters**, and **Scripts**.

### Devices
A **Device** is the top-level runtime object representing physical or logical equipment, such as:
- Robots
- PLCs
- CNC machines
- Safety systems
- Inspection systems
- Complete workcells

Devices are created in the Flexxbotics UI and are assigned a **Machine Model**, which determines which transformer is used for communication.

---

### Transformers
A **Transformer** is the software component responsible for communicating with equipment.

Each transformer includes:
- A JSON definition (metadata, Python file name, class name)
- A Python implementation
- Runtime methods mapped directly to the Device API

When a device is instantiated, its transformer is instantiated in the runtime and bound directly to that device.

---

### Machine Models
Machine Models act as the **configuration layer** that binds physical equipment to a transformer.

Machine model definitions specify:
- OEM
- Equipment model
- Controller type
- Transformer assignment

---

## Conceptual Architecture (Runtime Execution)

> This diagram is a **conceptual representation** of FlexxCore runtime execution.

```text
┌────────────────────────┐
│   Physical Equipment   │
│ (Robot, PLC, CNC, CMM) │
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│   Protocol / SDK Layer │
│ (TCP, OPC-UA, OEM API) │
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│      Transformer       │
│  - Status Reads        │
│  - Variables           │
│  - Commands            │
│  - Polling Logic       │
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│         Device         │
│   Runtime Object       │
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│    FlexxCore Device    │
│           API          │
└────────────────────────┘
```

## License

These open-source transformers are released under the **Apache 2.0 License**.  
You are not required to contribute modifications back, though the community always appreciates improvements that advance capabilities.

## Feedback

Let us know what you like about Flexxbotics Transformers—we want to hear from you.  
Even more importantly, tell us what you don’t like or what you’d like to see next. Your feedback and innovative ideas help shape the roadmap.


## Tags / Keywords

industrial automation · robotics · workcell automation · CNC · PLC · CMM ·  
industrial protocols · factory automation · robot integration ·  
FlexxCore · Flexxbotics · Python automation · interoperability · connectivity
