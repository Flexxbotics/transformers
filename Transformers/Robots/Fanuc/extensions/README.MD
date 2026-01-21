# Fanuc Robots
**Flexxbotics Platform Extensions for the Fanuc Robot**

---

## Setup

- Transfer flexx_core.pc and flexx_get_state.pc to the robot via USB. Ensure you LOAD the pc files onto the controller after transfering. 
  - flexx_core.pc : provides a tcp client that can communicate with the flexx core API via the tcp adapter container
  - flexx_get_state.pc : provides monitoring service for the robot to pull status data off the robot from the polling interval
- Setup socket communications:
  - Ensure option for socket messaging is installed on your robot
  - Ensure robot IP address is setup
  - Configure the Host Comm on C2: (TCP/IP Client)
    - Port: 7082




