#!/bin/bash

# Verification script to check handler protection status for all agents
# Run this before deployment to ensure A2A handlers are protected

set -e

AGENTCORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS_DIR="${AGENTCORE_DIR}/agents"

echo "🔍 Verifying Handler Protection Status"
echo "======================================"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PROTECTED_COUNT=0
TEMPLATE_COUNT=0
TOTAL_COUNT=0

# Iterate through all agent directories
for agent_dir in "$AGENTS_DIR"/*; do
    if [ -d "$agent_dir" ]; then
        agent_name=$(basename "$agent_dir")
        config_file="${agent_dir}/config.json"
        handler_file="${agent_dir}/handler.py"
        
        if [ -f "$config_file" ]; then
            TOTAL_COUNT=$((TOTAL_COUNT + 1))
            
            # Check handler protection status
            result=$(python3 -c "
import json
import sys

try:
    with open('${config_file}', 'r') as f:
        config = json.load(f)
    
    protocol = config.get('protocol', '').upper()
    use_template = config.get('use_handler_template', True)
    
    if protocol == 'A2A':
        print('A2A_PROTECTED')
    elif isinstance(use_template, str):
        if use_template.lower() in ['false', 'no', '0', 'off']:
            print('CUSTOM_PROTECTED')
        else:
            print('TEMPLATE')
    elif isinstance(use_template, bool):
        if not use_template:
            print('CUSTOM_PROTECTED')
        else:
            print('TEMPLATE')
    else:
        print('TEMPLATE')
except Exception as e:
    print('ERROR')
    print(str(e), file=sys.stderr)
")
            
            # Display result
            case "$result" in
                "A2A_PROTECTED")
                    echo -e "${GREEN}✅ $agent_name${NC}"
                    echo "   Status: A2A Protocol Handler (Protected)"
                    if [ -f "$handler_file" ]; then
                        echo "   Handler: Present"
                    else
                        echo -e "   ${YELLOW}⚠️  Handler: Missing (needs to be created)${NC}"
                    fi
                    PROTECTED_COUNT=$((PROTECTED_COUNT + 1))
                    ;;
                "CUSTOM_PROTECTED")
                    echo -e "${GREEN}✅ $agent_name${NC}"
                    echo "   Status: Custom Handler (Protected)"
                    if [ -f "$handler_file" ]; then
                        echo "   Handler: Present"
                    else
                        echo -e "   ${YELLOW}⚠️  Handler: Missing (needs to be created)${NC}"
                    fi
                    PROTECTED_COUNT=$((PROTECTED_COUNT + 1))
                    ;;
                "TEMPLATE")
                    echo -e "${BLUE}📄 $agent_name${NC}"
                    echo "   Status: Standard Template (Will be copied on build)"
                    TEMPLATE_COUNT=$((TEMPLATE_COUNT + 1))
                    ;;
                "ERROR")
                    echo -e "${YELLOW}⚠️  $agent_name${NC}"
                    echo "   Status: Error reading config"
                    ;;
            esac
            echo ""
        fi
    fi
done

echo "======================================"
echo "Summary:"
echo "  Total Agents: $TOTAL_COUNT"
echo "  Protected Handlers: $PROTECTED_COUNT (A2A + Custom)"
echo "  Template Handlers: $TEMPLATE_COUNT"
echo ""

if [ $PROTECTED_COUNT -gt 0 ]; then
    echo -e "${GREEN}✅ $PROTECTED_COUNT agent(s) have protected handlers${NC}"
    echo "   These handlers will NOT be overwritten during deployment"
fi

if [ $TEMPLATE_COUNT -gt 0 ]; then
    echo -e "${BLUE}📄 $TEMPLATE_COUNT agent(s) use the standard template${NC}"
    echo "   These handlers will be copied from agentcore/handler.template.py"
fi

echo ""
echo "To change handler protection:"
echo "  - For A2A agents: Set \"protocol\": \"A2A\" in config.json"
echo "  - For custom handlers: Set \"use_handler_template\": false in config.json"
echo "  - For template handlers: Set \"use_handler_template\": true (or omit)"
