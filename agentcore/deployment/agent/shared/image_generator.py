from mcp import stdio_client, StdioServerParameters
from strands import Agent, tool
import os
from strands_tools import generate_image, image_reader
from strands.tools.mcp import MCPClient

# Connect to an MCP server using stdio transport
# Note: uvx command syntax differs by platform


@tool
def generate_image_and_save_to_s3_and_return_presigned_url(prompt: str) -> str:
    """
    Generate an image using Amazon Nova Canvas, save it to S3, and return a presigned URL.

    Args:
        prompt: Text description of the image to generate

    Returns:
        JSON string with s3_key, s3_bucket, and presigned_url
    """
    import boto3
    import base64
    import json
    import uuid
    from datetime import datetime

    try:
        # Get configuration from environment
        bucket_name = f"{os.environ.get('STACK_PREFIX', 'demo')}-generated-content-{os.environ.get('UNIQUE_ID', 'default')}"
        region = os.environ.get("AWS_REGION", "us-east-1")

        # Initialize Bedrock Runtime client
        bedrock_runtime = boto3.client("bedrock-runtime", region_name=region)

        # Prepare Nova Canvas request
        request_body = {
            "taskType": "TEXT_IMAGE",
            "textToImageParams": {"text": prompt},
            "imageGenerationConfig": {
                "numberOfImages": 1,
                "quality": "standard",
                "height": 1024,
                "width": 1024,
                "cfgScale": 8.0,
                "seed": 0,
            },
        }

        # Invoke Nova Canvas model
        response = bedrock_runtime.invoke_model(
            modelId="amazon.nova-canvas-v1:0",
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )

        # Parse response
        response_body = json.loads(response["body"].read())

        # Extract base64 image from response
        if "images" in response_body and len(response_body["images"]) > 0:
            base64_image = response_body["images"][0]
        else:
            raise Exception("No image generated in response")

        # Generate unique key for the image
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        s3_key = f"images/{timestamp}_{unique_id}.png"

        # Decode base64 image
        image_data = base64.b64decode(base64_image)

        # Upload to S3
        s3_client = boto3.client("s3", region_name=region)
        s3_client.put_object(
            Bucket=bucket_name, Key=s3_key, Body=image_data, ContentType="image/png"
        )

        # Generate presigned URL (valid for 1 hour)
        presigned_url = s3_client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket_name, "Key": s3_key}, ExpiresIn=3600
        )

        # Return result as JSON
        result = {
            "s3_key": s3_key,
            "s3_bucket": bucket_name,
            "presigned_url": presigned_url,
            "prompt": prompt,
        }

        return json.dumps(result)

    except Exception as e:
        error_result = {
            "error": str(e),
            "s3_key": None,
            "s3_bucket": bucket_name if "bucket_name" in locals() else None,
            "presigned_url": None,
        }
        return json.dumps(error_result)


@tool
def generate_image_from_descriptions(prompt: str) -> str:
    """
    Generate images by invoking the CreativeImageGenerator Lambda function.
    
    Args:
        prompt: Text description(s) of the image(s) to generate. Can be a single description
                or comma-separated descriptions for multiple images.
    
    Returns:
        JSON string with array of pending image records containing content_id, status, and other metadata
    """
    import boto3
    import json
    
    try:
        # Get Lambda function name from environment
        stack_prefix = os.environ.get("STACK_PREFIX", "demo")
        unique_id = os.environ.get("UNIQUE_ID", "default")
        region = os.environ.get("AWS_REGION", "us-east-1")
        
        lambda_function_name = f"{stack_prefix}-CreativeImageGenerator-{unique_id}"
        
        # Parse prompt into descriptions array
        descriptions = [desc.strip() for desc in prompt.split(',')]
        
        # Prepare Lambda payload matching the expected format
        payload = {
            'actionGroup': 'ImageGenerationActionGroup',
            'function': 'generate_creative_image',
            'messageVersion': '1.0',
            'parameters': [
                {
                    'name': 'descriptions',
                    'value': descriptions,
                    'type': 'array'
                },
                {
                    'name': 'width',
                    'value': '1024',
                    'type': 'string'
                },
                {
                    'name': 'height',
                    'value': '1024',
                    'type': 'string'
                }
            ]
        }
        
        # Invoke Lambda function
        lambda_client = boto3.client('lambda', region_name=region)
        response = lambda_client.invoke(
            FunctionName=lambda_function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )
        
        # Parse response
        response_payload = json.loads(response['Payload'].read())
        
        # Extract the function response body
        if 'response' in response_payload:
            function_response = response_payload['response'].get('functionResponse', {})
            response_body = function_response.get('responseBody', {})
            text_body = response_body.get('TEXT', {}).get('body', '[]')
            pending_records = json.loads(text_body)
            
            # Format response for the agent
            result = {
                'status': 'success',
                'message': f'Successfully queued {len(pending_records)} image(s) for generation',
                'images': pending_records
            }
            return json.dumps(result)
        else:
            # Handle error response
            error_msg = response_payload.get('body', {}).get('application/json', {}).get('body', str(response_payload))
            return json.dumps({
                'status': 'error',
                'message': f'Error from Lambda: {error_msg}',
                'images': []
            })
            
    except Exception as e:
        error_result = {
            'status': 'error',
            'message': f'Failed to invoke image generator: {str(e)}',
            'images': []
        }
        return json.dumps(error_result)
