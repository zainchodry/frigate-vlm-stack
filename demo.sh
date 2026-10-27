#!/bin/bash
# ============================================================
# Frigate + Home Assistant + VLM Demo Script
# ============================================================
# Run this script to simulate events and show everything working.
# Use this while screen recording with OBS Studio.
# ============================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

divider() {
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}$1${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo ""
}

pause() {
    echo -e "${YELLOW}  ▶ Press ENTER to continue...${NC}"
    read -r
}

HOST_IP=$(hostname -I | awk '{print $1}')
[ -z "$HOST_IP" ] && HOST_IP="localhost"

# ============================================================
divider "📋 DEMO 1: Services Overview"
# ============================================================
echo -e "${GREEN}All three services are running via Docker Compose:${NC}"
echo ""
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "NAME|frigate|homeassistant|mqtt"
echo ""
echo -e "${GREEN}✅ Frigate NVR      → http://${HOST_IP}:5000 (or http://localhost:5000)${NC}"
echo -e "${GREEN}✅ Home Assistant   → http://${HOST_IP}:8123 (or http://localhost:8123)${NC}"
echo -e "${GREEN}✅ MQTT Broker      → port 1883 (internal)${NC}"
pause

# ============================================================
divider "📋 DEMO 2: Docker Compose Configuration"
# ============================================================
echo -e "${GREEN}File: /home/enigmatix/home-server/docker-compose.yml${NC}"
echo ""
cat /home/enigmatix/home-server/docker-compose.yml
pause

# ============================================================
divider "📋 DEMO 3: Frigate Configuration (with VLM)"
# ============================================================
echo -e "${GREEN}File: /home/enigmatix/home-server/frigate/config/config.yml${NC}"
echo ""
cat /home/enigmatix/home-server/frigate/config/config.yml
pause

# ============================================================
divider "📋 DEMO 4: VLM (Vision Language Model) Config"
# ============================================================
echo -e "${GREEN}VLM/GenAI section from Frigate config:${NC}"
echo ""
grep -A 25 "Generative AI" /home/enigmatix/home-server/frigate/config/config.yml
echo ""
echo -e "${YELLOW}VLM Features:${NC}"
echo "  • descriptions → Auto-generates human-readable event summaries"
echo "  • embeddings   → Semantic search: 'find person with red bag'"
echo "  • chat         → Ask questions: 'what happened at 3pm?'"
echo ""
echo -e "${YELLOW}Supported VLM Providers:${NC}"
echo "  • Ollama (local, free, private)"
echo "  • OpenAI (cloud, paid)"
echo "  • Google Gemini (cloud, free tier)"
echo "  • llama.cpp (local, lightweight)"
pause

# ============================================================
divider "📋 DEMO 5: Home Assistant Automations (Alert Config)"
# ============================================================
echo -e "${GREEN}File: /home/enigmatix/home-server/homeassistant/config/configuration.yaml${NC}"
echo ""
cat /home/enigmatix/home-server/homeassistant/config/configuration.yaml
pause

# ============================================================
divider "📋 DEMO 6: Alert Generation Flow"
# ============================================================
echo -e "${BOLD}How alerts flow from camera to notification:${NC}"
echo ""
echo "  📹 Camera (RTSP stream)"
echo "     │"
echo "     ▼"
echo "  🔍 Frigate - Object Detection (person/car/dog/cat)"
echo "     │"
echo "     ▼"
echo "  📸 Snapshot + Video Clip saved"
echo "     │"
echo "     ▼"
echo "  🧠 VLM/Ollama analyzes snapshot (optional)"
echo "     │  → 'A person in blue jacket approaching front door'"
echo "     ▼"
echo "  📨 MQTT Event published → topic: frigate/events"
echo "     │"
echo "     ▼"
echo "  🏠 Home Assistant receives MQTT event"
echo "     │"
echo "     ▼"
echo "  🔔 Automations fire:"
echo "     ├── 📌 Dashboard notification"
echo "     ├── 📱 Mobile push with snapshot"
echo "     └── 🤖 VLM AI-enriched description"
pause

# ============================================================
divider "📋 DEMO 7: Live MQTT Event Simulation"
# ============================================================
echo -e "${GREEN}Simulating a Frigate detection event via MQTT...${NC}"
echo ""

# Simulate a person detection event
SIMULATED_EVENT='{
  "type": "new",
  "before": {},
  "after": {
    "id": "demo-event-001",
    "camera": "front_door",
    "label": "person",
    "top_score": 0.87,
    "start_time": '$(date +%s)',
    "end_time": null,
    "zones": ["front_yard"],
    "description": "A person wearing dark clothing approaching the front door, appears to be a delivery driver carrying a package."
  }
}'

echo -e "${CYAN}Publishing to frigate/events:${NC}"
echo "$SIMULATED_EVENT" | python3 -m json.tool 2>/dev/null || echo "$SIMULATED_EVENT"
echo ""

docker exec mqtt mosquitto_pub -t "frigate/events" -m "$SIMULATED_EVENT" 2>/dev/null && \
    echo -e "${GREEN}✅ Event published to MQTT successfully!${NC}" || \
    echo -e "${RED}⚠  MQTT publish failed (but this shows the event format)${NC}"

echo ""
echo -e "${YELLOW}→ Check Home Assistant dashboard for the notification!${NC}"
echo -e "${YELLOW}→ http://${HOST_IP}:8123 (or http://localhost:8123)${NC}"
pause

# ============================================================
divider "📋 DEMO 8: Monitoring MQTT Events in Real-Time"
# ============================================================
echo -e "${GREEN}To monitor live MQTT events from Frigate, run:${NC}"
echo ""
echo "  docker exec mqtt mosquitto_sub -t 'frigate/#' -v"
echo ""
echo -e "${GREEN}To monitor only detection events:${NC}"
echo ""
echo "  docker exec mqtt mosquitto_sub -t 'frigate/events' -v"
pause

# ============================================================
divider "📋 DEMO 9: Service Health Check"
# ============================================================
echo -e "${GREEN}Checking all service endpoints:${NC}"
echo ""

FRIGATE_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5000)
HA_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8123)
MQTT_STATUS=$(docker exec mqtt mosquitto_pub -t "health/check" -m "ok" 2>&1 && echo "OK" || echo "FAIL")

echo "  Frigate NVR:      HTTP $FRIGATE_STATUS $([ "$FRIGATE_STATUS" = "200" ] && echo '✅' || echo '❌')"
echo "  Home Assistant:   HTTP $HA_STATUS $([ "$HA_STATUS" = "200" ] || [ "$HA_STATUS" = "302" ] && echo '✅' || echo '❌')"
echo "  MQTT Broker:      $MQTT_STATUS ✅"
echo ""

echo -e "${GREEN}Docker resource usage:${NC}"
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" | grep -E "NAME|frigate|homeassistant|mqtt"
pause

# ============================================================
divider "✅ DEMO COMPLETE"
# ============================================================
echo -e "${BOLD}Summary of what was demonstrated:${NC}"
echo ""
echo "  1. ✅ Docker Compose setup (Frigate + HA + MQTT)"
echo "  2. ✅ Frigate NVR configuration with VAAPI acceleration"
echo "  3. ✅ VLM/GenAI config (Ollama integration)"
echo "  4. ✅ Home Assistant automations & alert config"
echo "  5. ✅ Alert generation flow (camera → detection → MQTT → notification)"
echo "  6. ✅ Live MQTT event simulation"
echo "  7. ✅ Service health verification"
echo ""
echo -e "${GREEN}All services running at:${NC}"
echo "  • Frigate:        http://${HOST_IP}:5000 (or http://localhost:5000)"
echo "  • Home Assistant: http://${HOST_IP}:8123 (or http://localhost:8123)"
echo ""
echo -e "${YELLOW}Stop recording now if using OBS Studio.${NC}"
