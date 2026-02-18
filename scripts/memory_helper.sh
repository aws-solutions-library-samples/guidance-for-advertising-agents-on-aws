#!/bin/bash
# Memory Helper Script - Quick commands for memory retrieval

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEMORY_SCRIPT="$SCRIPT_DIR/test_memory_retrieval.py"

# Default values
DEFAULT_PROFILE="rtbag"
DEFAULT_REGION="us-east-1"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

usage() {
    echo -e "${BLUE}Memory Helper Script${NC}"
    echo "Usage: $0 [COMMAND] [OPTIONS]"
    echo ""
    echo "Commands:"
    echo "  list-records                     List all memory records"
    echo "  get-memory <memory-id>           Get memory details"
    echo "  get-events <memory-id>           Get memory events"
    echo "  get-all <memory-id>              Get memory + events"
    echo "  get-event <memory-id> <event-id> Get specific event details"
    echo ""
    echo "Options:"
    echo "  -p, --profile PROFILE            AWS profile (default: $DEFAULT_PROFILE)"
    echo "  -r, --region REGION              AWS region (default: $DEFAULT_REGION)"
    echo "  -o, --output FILE                Save to file"
    echo "  -j, --json                       JSON output format"
    echo "  -v, --verbose                    Verbose output"
    echo "  -h, --help                       Show this help"
    echo ""
    echo "Examples:"
    echo "  $0 list-records"
    echo "  $0 get-memory mem-12345"
    echo "  $0 get-events mem-12345 -p myprofile -o events.txt"
    echo "  $0 get-all mem-12345 --json --output full_data.json"
    echo "  $0 get-event mem-12345 evt-67890 --verbose"
}

# Parse arguments
COMMAND=""
MEMORY_ID=""
EVENT_ID=""
PROFILE="$DEFAULT_PROFILE"
REGION="$DEFAULT_REGION"
OUTPUT=""
FORMAT="text"
VERBOSE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        list-records|get-memory|get-events|get-all|get-event)
            COMMAND="$1"
            shift
            ;;
        -p|--profile)
            PROFILE="$2"
            shift 2
            ;;
        -r|--region)
            REGION="$2"
            shift 2
            ;;
        -o|--output)
            OUTPUT="$2"
            shift 2
            ;;
        -j|--json)
            FORMAT="json"
            shift
            ;;
        -v|--verbose)
            VERBOSE="--verbose"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            if [[ -z "$MEMORY_ID" ]]; then
                MEMORY_ID="$1"
            elif [[ -z "$EVENT_ID" && "$COMMAND" == "get-event" ]]; then
                EVENT_ID="$1"
            else
                echo -e "${RED}Unknown argument: $1${NC}"
                usage
                exit 1
            fi
            shift
            ;;
    esac
done

# Validate command
if [[ -z "$COMMAND" ]]; then
    echo -e "${RED}Error: No command specified${NC}"
    usage
    exit 1
fi

# Validate memory ID (not required for list-records)
if [[ -z "$MEMORY_ID" && "$COMMAND" != "list-records" ]]; then
    echo -e "${RED}Error: Memory ID required for $COMMAND${NC}"
    usage
    exit 1
fi

# Validate event ID for get-event command
if [[ "$COMMAND" == "get-event" && -z "$EVENT_ID" ]]; then
    echo -e "${RED}Error: Event ID required for get-event command${NC}"
    usage
    exit 1
fi

# Build command arguments
ARGS="--profile $PROFILE --region $REGION --format $FORMAT"

if [[ -n "$MEMORY_ID" ]]; then
    ARGS="$ARGS --memory-id $MEMORY_ID"
fi

if [[ -n "$OUTPUT" ]]; then
    ARGS="$ARGS --output $OUTPUT"
fi

if [[ -n "$VERBOSE" ]]; then
    ARGS="$ARGS $VERBOSE"
fi

if [[ "$COMMAND" == "get-event" ]]; then
    ARGS="$ARGS --event-id $EVENT_ID"
fi

if [[ "$COMMAND" == "list-records" ]]; then
    ARGS="$ARGS --list-records"
fi

# Execute based on command
echo -e "${BLUE}Executing: $COMMAND${NC}"
echo -e "${YELLOW}Memory ID: $MEMORY_ID${NC}"
if [[ -n "$EVENT_ID" ]]; then
    echo -e "${YELLOW}Event ID: $EVENT_ID${NC}"
fi
echo -e "${YELLOW}Profile: $PROFILE, Region: $REGION${NC}"
echo ""

case $COMMAND in
    list-records)
        python3 "$MEMORY_SCRIPT" $ARGS
        ;;
    get-memory)
        python3 "$MEMORY_SCRIPT" $ARGS --max-events 0
        ;;
    get-events)
        python3 "$MEMORY_SCRIPT" $ARGS
        ;;
    get-all)
        python3 "$MEMORY_SCRIPT" $ARGS
        ;;
    get-event)
        python3 "$MEMORY_SCRIPT" $ARGS
        ;;
    *)
        echo -e "${RED}Unknown command: $COMMAND${NC}"
        usage
        exit 1
        ;;
esac

echo -e "\n${GREEN}✅ Command completed successfully${NC}"