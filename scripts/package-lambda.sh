#!/bin/bash

# Lambda Function Packaging Script
# This script packages Lambda functions for deployment

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LAMBDA_DIR="${PROJECT_ROOT}/lambda"
BUILD_DIR="${PROJECT_ROOT}/build/lambda"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

# Function to package a Lambda function
package_lambda() {
    local function_name=$1
    local python_file=$2
    
    print_status "Packaging Lambda function: $function_name"
    
    # Create build directory
    local function_build_dir="${BUILD_DIR}/${function_name}"
    mkdir -p "$function_build_dir"
    
    # Copy Python file
    if [ ! -f "${LAMBDA_DIR}/${python_file}" ]; then
        print_error "Lambda source file not found: ${LAMBDA_DIR}/${python_file}"
        return 1
    fi
    
    cp "${LAMBDA_DIR}/${python_file}" "$function_build_dir/"
    
    # Create ZIP file
    ZIP_FILE="${BUILD_DIR}/${function_name}.zip"
    cd "$function_build_dir"
    zip -r "$ZIP_FILE" . > /dev/null 2>&1
    cd - > /dev/null
    
    print_success "✅ Packaged $function_name to $ZIP_FILE"
    return 0
}

# Function to upload Lambda package to S3
upload_lambda_to_s3() {
    local zip_file=$1
    local s3_bucket=$2
    local s3_key=$3
    local aws_profile=$4
    local aws_region=$5
    
    print_status "Uploading Lambda package to S3..."
    
    # Build AWS command properly
    if [ -n "$aws_profile" ] && [ "$aws_profile" != "default" ]; then
        if aws --profile "$aws_profile" s3 cp "$zip_file" "s3://$s3_bucket/$s3_key" --region "$aws_region"; then
            print_success "✅ Uploaded to s3://$s3_bucket/$s3_key"
            return 0
        else
            print_error "❌ Failed to upload to S3"
            return 1
        fi
    else
        if aws s3 cp "$zip_file" "s3://$s3_bucket/$s3_key" --region "$aws_region"; then
            print_success "✅ Uploaded to s3://$s3_bucket/$s3_key"
            return 0
        else
            print_error "❌ Failed to upload to S3"
            return 1
        fi
    fi
}

# Function to update Lambda function code
update_lambda_function() {
    local function_name=$1
    local s3_bucket=$2
    local s3_key=$3
    local aws_profile=$4
    local aws_region=$5
    
    print_status "Updating Lambda function code: $function_name"
    
    # Build AWS command properly
    if [ -n "$aws_profile" ] && [ "$aws_profile" != "default" ]; then
        if aws --profile "$aws_profile" lambda update-function-code \
            --function-name "$function_name" \
            --s3-bucket "$s3_bucket" \
            --s3-key "$s3_key" \
            --region "$aws_region"; then
            print_success "✅ Updated Lambda function: $function_name"
            return 0
        else
            print_error "❌ Failed to update Lambda function"
            return 1
        fi
    else
        if aws lambda update-function-code \
            --function-name "$function_name" \
            --s3-bucket "$s3_bucket" \
            --s3-key "$s3_key" \
            --region "$aws_region"; then
            print_success "✅ Updated Lambda function: $function_name"
            return 0
        else
            print_error "❌ Failed to update Lambda function"
            return 1
        fi
    fi
}

# Main function
main() {
    local function_name=${1:-"async-image-processor"}
    local python_file=${2:-"async_image_processor.py"}
    local s3_bucket=$3
    local stack_prefix=${4:-"sim"}
    local unique_id=$5
    local aws_profile=$6
    local aws_region=${7:-"us-west-2"}
    
    print_status "=========================================="
    print_status "🚀 LAMBDA FUNCTION PACKAGING"
    print_status "=========================================="
    print_status "Function: $function_name"
    print_status "Source: $python_file"
    print_status "S3 Bucket: ${s3_bucket:-'(not specified)'}"
    print_status "Stack Prefix: $stack_prefix"
    print_status "Unique ID: ${unique_id:-'(not specified)'}"
    print_status "AWS Profile: ${aws_profile:-'(default)'}"
    print_status "AWS Region: $aws_region"
    echo ""
    
    # Package the Lambda function
    if ! package_lambda "$function_name" "$python_file"; then
        print_error "Failed to package Lambda function"
        exit 1
    fi
    
    local zip_file="$ZIP_FILE"
    
    # If S3 bucket is provided, upload and update
    if [ -n "$s3_bucket" ] && [ -n "$unique_id" ]; then
        local s3_key="lambda/${function_name}.zip"
        local lambda_function_name="${stack_prefix}-${function_name}-${unique_id}"
        
        # Upload to S3
        upload_lambda_to_s3 "$zip_file" "$s3_bucket" "$s3_key" "$aws_profile" "$aws_region"
            # Update Lambda function
            #update_lambda_function "$lambda_function_name" "$s3_bucket" "$s3_key" "$aws_profile" "$aws_region"
    else
        print_status "S3 bucket or unique ID not provided, skipping upload and update"
        print_status "ZIP file available at: $zip_file"
    fi
    
    print_success "🎉 Lambda packaging completed!"
}

# Show usage if no arguments
if [ $# -eq 0 ]; then
    echo "Usage: $0 [function_name] [python_file] [s3_bucket] [stack_prefix] [unique_id] [aws_profile] [aws_region]"
    echo ""
    echo "Examples:"
    echo "  $0 async-image-processor async_image_processor.py"
    echo "  $0 async-image-processor async_image_processor.py my-bucket sim abc123 default us-west-2"
    echo ""
    exit 0
fi

# Run main function with all arguments
main "$@"