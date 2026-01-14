# Flexxbotics Transformers
**Industrial Device & Workcell Transformers for the FlexxCore Platform**

![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Language](https://img.shields.io/badge/language-Python-blue)
![Domain](https://img.shields.io/badge/domain-Industrial%20Automation-informational)
![Platform](https://img.shields.io/badge/platform-FlexxCore-success)

---

<p align="center">
  <img src="Documentation/Flexxbotics_Logo.png"
       alt="Flexxbotics Logo"
       style="width: 100%; max-width: 800px;" />
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

- Open source equipment transformers for use with Flexxbotics software:
  
| Name | Description | File |
|------|------------|------|
| OPC UA | Protocol definition for communication with OPC-UA servers | opcua.py |
| MTConnect | Protocol definition for communication with MTConnect agents | mtconnect.py |
| MQTT | Protocol definition for communication with MQTT brokers | mqtt.py |
| TCP/IP | Protocol definition for TCP/IP socket communication | tcp.py |
| Modbus-TCP | Protocol definition for communication with Modbus-TCP enabled devices | modbus.py |
| Siemens S7comm | Protocol definition for communication with S7comm based Siemens PLCs | siemens_s7comm.py |
| Allen Bradley EIP-Logix | Protocol definition for communication with EIP-Logix based Allen Bradley PLCs | allen_bradley_eip-logix.py |
| Beckhoff ADS Twincat | Protocol definition for communication with ADS Twincat based Beckhoff PLCs | beckhoff_ads_twincat.py |
| Serial | Protocol definition for communication with serial devices | serial.py |
| Universal Robots | Connector driver for all models of the Universal Robot 6-axis robotic arms | ur.py |
| FANUC Robots | Connector driver for all models of FANUC 6-axis robotic arms (industrial & collaborative) | fanuc.py |
| Haas Next Gen (NGC) | Connector driver for all models of Haas machines using the Next Gen Controller | haas_next_gen.py |
| Haas Serial | Connector driver for all models of Haas machines using the legacy controller | haas_serial.py |
| FOCAS2 | Connector driver for all machines using I-Series FANUC-based controllers | focas2.py |
| Heidenhain TNC | Connector driver for TNCRemo support for Heidenhain TNC-based controllers | heidenhain_tnc530.py |
| Yaskawa | Connector driver for Modbus-TCP enabled communication with Yaskawa MP2600 motor controllers | yaskawa_mp2600.py |
| Okuma | Connector driver for Okuma OSP300 and later controllers | okuma.py |
| Hexagon | Connector driver for Hexagon CMMs | hexagon.py |
| Keyence Telecentric | Connector driver for Keyence telecentric measurement devices | keyence_telecentric.py |
| Keyence Profiler | Connector driver for Keyence profile sensors | keyence_profiler.py |
| COGNEX | Connector driver for Modbus-TCP enabled communication with Cognex In-Sight cameras | cognex_camera.py |
| TRUMPF Laser Marker | Connector driver for Trumpf laser markers | trumpf_laser.py |
| FOBA | Connector driver for the M-Series FOBA lasers with Mark-US TCP interface | foba.py |
| SICK | Connector driver for the SICK FlexiCompact safety PLC | flexi_compact.py |
| WAGO | Connector driver for Modbus-TCP enabled communication with Wago I/O blocks | wago.py |
| Sturtevant Richmont | Connector driver for the Sturtevant Richmont Global400 torque controller interface | global400.py |

- Example of a base **Transformer Template** for creating your own transformer
- Example of a **Workcell Transformer**, which embeds multiple transformers to:
  - Reconcile workcell state  
  - Perform automatic restarts  
  - Run threaded controls  
  - Provide many additional advanced capabilities
- Example an **Automation Script** callable from the Flexxbotics HMI Controls Configurator or run in realtime that can communicate with transformers to perform automation tasks.

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
