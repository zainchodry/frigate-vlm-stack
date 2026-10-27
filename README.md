# 🏠 AI-Powered Smart Home Server: Frigate NVR + Home Assistant

An all-in-one, privacy-focused home surveillance and automation platform powered by **Frigate NVR**, **Home Assistant**, and **Mosquitto MQTT**. Features real-time AI object detection, hardware-accelerated video streaming with `go2rtc`, custom **Face Recognition** (FaceNet embeddings), and intelligent event alerts.

---

## 📑 Table of Contents
- [Architecture Overview](#-architecture-overview)
- [Key Features](#-key-features)
- [Service Endpoints](#-service-endpoints)
- [Directory Structure](#-directory-structure)
- [Prerequisites](#-prerequisites)
- [Quick Start](#-quick-start)
- [Camera Setup (iPhone / RTSP)](#-camera-setup-iphone--rtsp)
- [Face Recognition System](#-face-recognition-system)
- [Home Assistant Integration & Automations](#-home-assistant-integration--automations)
- [Demo Script](#-demo-script)
- [Troubleshooting & Maintenance](#-troubleshooting--maintenance)

---

## 🏛 Architecture Overview

```mermaid
flowchart TD
    subgraph CameraSource ["📹 Video Source"]
        Cam["iPhone / IP Camera\n(IP Camera Lite @ 192.168.0.133:8081)"]
    end

    subgraph FrigateStack ["🧠 Frigate NVR (Port 5000)"]
        G2R["go2rtc\n(RTSP Re-stream & WebRTC)"]
        Det["Object Detector\n(CPU-based: person, car, dog, cat)"]
        Face["FaceNet Recognition\n(ONNX Embeddings: Zain & Mehtab)"]
        Cam -->|MJPEG / RTSP| G2R
        G2R --> Det
        Det --> Face
    end

    subgraph MessageBroker ["📨 Mosquitto MQTT (Port 1883)"]
        MQTT["frigate/events\n(JSON Event Stream)"]
        Face -->|Publish Event + Sub-Label| MQTT
    end

    subgraph SmartHome ["🏡 Home Assistant (Port 8123)"]
        HAC["Frigate Custom Integration\n(camera.iphone_camera, sensors)"]
        Auto["Automations Engine\n(Face Alert: Zain, Mehtab, Unknown)"]
        MQTT --> HAC
        MQTT --> Auto
    end
```

---

## ✨ Key Features

1. **Ultra-Low Latency Video Streaming (`go2rtc`)**:
   - Ingests RTSP, RTMP, HTTP MJPEG, or HomeKit feeds.
   - Low-latency WebRTC and MSE playback on Web & Mobile dashboards.
2. **AI Object Detection**:
   - Multi-threaded CPU object detector tracking `person`, `car`, `dog`, and `cat`.
   - Hardware-accelerated decoding support via Intel VAAPI (`/dev/dri/renderD128`).
3. **Face Recognition (FaceNet ONNX Embeddings)**:
   - Evaluates face crops in real-time.
   - Accurately matches against stored libraries (e.g., **Zain** and **Mehtab**).
   - Dynamically attaches the recognized person's name as the event's `sub_label`.
4. **Seamless Home Assistant Integration**:
   - Official `frigate-hass-integration` delivers live camera feeds, occupancy binary sensors, motion entities, and snapshot controls.
   - Custom YAML automations trigger immediate notifications when a recognized family member or an unknown visitor arrives.
5. **Vision-Language Model (VLM / GenAI) Ready**:
   - Configuration template provided for local Ollama (`qwen3-vl:4b`) integration to enable automatic scene descriptions and semantic natural-language event search.

---

## 🌐 Service Endpoints

| Service | Protocol / Port | Access URL | Description |
| :--- | :--- | :--- | :--- |
| **Frigate NVR** | HTTP `5000` | `http://<SERVER_IP>:5000` | Camera feeds, detection review, face recognition |
| **Home Assistant** | HTTP `8123` | `http://<SERVER_IP>:8123` | Smart home dashboard, automations, notifications |
| **Mosquitto MQTT** | TCP `1883` | `<SERVER_IP>:1883` | Message bus between Frigate and Home Assistant |
| **go2rtc RTSP** | TCP `8554` | `rtsp://<SERVER_IP>:8554` | Re-streamed RTSP feeds |
| **WebRTC Stream** | TCP/UDP `8555` | — | Ultra-low latency camera live view |

---

## 📁 Directory Structure

```text
home-server/
├── docker-compose.yml              # Multi-container orchestration definition
├── demo.sh                         # Interactive terminal demo & inspection script
├── .gitignore                      # Git ignore rules for media, logs, databases & secrets
├── frigate/
│   ├── config/
│   │   └── config.yml              # Frigate NVR cameras, detectors, and face recognition
│   └── storage/
│       ├── clips/                  # Detection events, snapshots, and face datasets
│       │   └── faces/
│       │       ├── Zain/           # Face library images for Zain
│       │       └── Mehtab/         # Face library images for Mehtab
│       └── recordings/             # 24/7 and event video segment archives
├── homeassistant/
│   └── config/
│       ├── configuration.yaml      # Home Assistant core config & notification automations
│       └── custom_components/
│           └── frigate/            # Frigate custom integration (v5.15.6)
└── mqtt/
    ├── config/
    │   └── mosquitto.conf          # Mosquitto broker configuration
    ├── data/                       # Mosquitto persistence database
    └── log/                        # Mosquitto broker logs
```

---

## ⚙️ Prerequisites

- **Host OS**: Ubuntu Linux (or any Linux distribution with Docker support).
- **Docker Engine & Docker Compose**:
  ```bash
  sudo apt-get update
  sudo apt-get install docker.io docker-compose-plugin
  ```
- **IP Camera or Smartphone**:
  - Smartphone app providing RTSP or MJPEG (e.g. *IP Camera Lite* on iOS/Android).

---

## 🚀 Quick Start

1. **Clone or Navigate to the Project Directory**:
   ```bash
   cd ~/home-server
   ```

2. **Start the Stack**:
   ```bash
   docker compose up -d
   ```

3. **Verify Container Health**:
   ```bash
   docker compose ps
   ```

4. **Access the Web Interfaces**:
   - Frigate NVR: [http://localhost:5000](http://localhost:5000)
   - Home Assistant: [http://localhost:8123](http://localhost:8123)

---

## 📱 Camera Setup (iPhone / RTSP)

Frigate uses `go2rtc` for stream negotiation. In [frigate/config/config.yml](frigate/config/config.yml):

```yaml
go2rtc:
  streams:
    iphone_camera:
      - "http://admin:admin@192.168.0.133:8081/video"

cameras:
  iphone_camera:
    ffmpeg:
      inputs:
        - path: "rtsp://127.0.0.1:8554/iphone_camera"
          roles:
            - detect
            - record
    detect:
      enabled: true
      width: 1920
      height: 1080
      fps: 5
```

> **Note**: Replace `192.168.0.133:8081` with the IP address and port displayed in your camera app.

---

## 👤 Face Recognition System

Frigate uses an onnx-based FaceNet neural network to detect faces, extract vector embeddings, and compute cosine distances against stored face sets.

### Configuration
In [frigate/config/config.yml](frigate/config/config.yml):

```yaml
face_recognition:
  enabled: true
  model_size: small            # CPU-friendly FaceNet model
  detection_threshold: 0.60    # Face detector confidence threshold
  recognition_threshold: 0.80  # Recognition confidence threshold to apply name
  unknown_score: 0.65          # Distance threshold for unknown faces
  min_area: 400                # Minimum face crop size (pixels)
  min_faces: 1                 # 1 frame match immediately labels person
  save_attempts: 100
```

### Adding New People
Face training libraries are stored under:
```text
frigate/storage/clips/faces/<PersonName>/
```
- For **Zain**: Place 3–5 well-lit face photos in `frigate/storage/clips/faces/Zain/`
- For **Mehtab**: Place 3–5 well-lit face photos in `frigate/storage/clips/faces/Mehtab/`

### Verifying Recognition via API
Test an image against the active model with:
```bash
curl -X POST -F "file=@face_sample.png" http://localhost:5000/api/faces/recognize
```

---

## 🔔 Home Assistant Integration & Automations

The integration operates via MQTT:
1. When Frigate detects an object, it publishes event metadata to topic `frigate/events`.
2. When a face matches, Frigate updates `sub_label` with the person's name (e.g. `"Zain"` or `"Mehtab"`).
3. Home Assistant triggers automations defined in [homeassistant/config/configuration.yaml](homeassistant/config/configuration.yaml):

- **Zain Recognized**:
  ```yaml
  condition:
    - condition: template
      value_template: "{{ 'zain' in (trigger.payload_json['after']['sub_label'] | string | lower) }}"
  action:
    - service: persistent_notification.create
      data:
        title: "👤 Zain Recognized — iPhone Camera"
        message: "Zain has been recognized on the iPhone Camera at {{ now().strftime('%H:%M:%S') }}."
  ```

- **Mehtab Recognized**:
  ```yaml
  condition:
    - condition: template
      value_template: "{{ 'mehtab' in (trigger.payload_json['after']['sub_label'] | string | lower) }}"
  action:
    - service: persistent_notification.create
      data:
        title: "👤 Mehtab Recognized — iPhone Camera"
        message: "Mehtab has been recognized on the iPhone Camera at {{ now().strftime('%H:%M:%S') }}."
  ```

- **Unknown Person Detected**:
  ```yaml
  condition:
    - condition: template
      value_template: "{{ trigger.payload_json['after']['sub_label'] is none or trigger.payload_json['after']['sub_label'] == '' }}"
  action:
    - service: persistent_notification.create
      data:
        title: "🚶 Unknown Person Detected — iPhone Camera"
        message: "An unidentified person was detected on iPhone Camera."
  ```

---

## 🎬 Demo Script

An interactive demonstration script is included to quickly test and inspect all services:

```bash
chmod +x demo.sh
./demo.sh
```

The script guides you through:
1. Services health check
2. Docker Compose inspection
3. Frigate and VLM configuration review
4. Home Assistant automations walkthrough
5. Live event stream simulation

---

## 🛠 Troubleshooting & Maintenance

### Restart Frigate Gracefully
```bash
curl -X POST http://localhost:5000/api/restart
```

### Inspect Frigate Logs
```bash
docker compose logs -f frigate
```

### Inspect MQTT Messages
To monitor live events published by Frigate:
```bash
docker run --rm eclipse-mosquitto:2 mosquitto_sub -h 172.80.1.1 -t "frigate/#" -v
```

### Check Home Assistant Status
```bash
curl -s http://localhost:8123/manifest.json
```

---

## 📄 License
This setup is maintained for personal and private home surveillance. Distributed under the MIT License.
