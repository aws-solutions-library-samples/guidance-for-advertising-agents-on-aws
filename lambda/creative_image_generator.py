import json
import boto3
import uuid
import os
from datetime import datetime
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

class ImageGeneratorService:
    def __init__(self):
        self.lambda_client = boto3.client('lambda')
        self.bucket_name = os.environ.get('GENERATED_CONTENT_BUCKET')
        self.table_name = os.environ['GENERATED_CONTENT_TABLE']
        self.async_processor_function = os.environ.get('ASYNC_IMAGE_PROCESSOR_FUNCTION', 'async-image-processor')
        self.table = boto3.resource('dynamodb').Table(self.table_name)
    
    def invoke_async_processor(self, image_jobs):
        """Invoke the async image processor Lambda function"""
        try:
            payload = {
                'image_jobs': image_jobs
            }
            
            # Invoke the async processor function
            response = self.lambda_client.invoke(
                FunctionName=self.async_processor_function,
                InvocationType='Event',  # Asynchronous invocation
                Payload=json.dumps(payload)
            )
            
            logger.info(f"Successfully invoked async processor for {len(image_jobs)} jobs")
            return True
            
        except Exception as e:
            logger.error(f"Error invoking async processor: {str(e)}")
            raise

    def create_pending_record(self, content_id, prompt, full_image_key, thumbnail_key):
        """Create a pending record in DynamoDB"""
        try:
            item = {
                'content_id': content_id,
                'content_type': 'image',
                'status': 'pending',
                'original_url': '',
                'thumbnail_url': '',
                'prompt': prompt,
                'key': full_image_key,
                'bucket': self.bucket_name,
                'created_date': datetime.now().isoformat()
            }
            
            self.table.put_item(Item=item)
            logger.info(f"Created pending record for content_id: {content_id}")
            return item
            
        except Exception as e:
            logger.error(f"Error creating pending record: {str(e)}")
            raise

def lambda_handler(event, context):
    """Main Lambda handler for image generation actions"""
    try:
        logger.info(f"Received event: {json.dumps(event)}")
        
        # Parse the action and parameters
        action = event.get('actionGroup', '')
        function_name = event.get('function', '')
        parameters = event.get('parameters', [])
        message_version = event.get('messageVersion',1)
        
        # Initialize the service
        service = ImageGeneratorService()
        
        return handle_generate_creative_image(action, function_name, message_version, service, parameters)
            
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': {
                'application/json': {
                    'body': json.dumps({
                        'error': f'Internal server error: {str(e)}'
                    })
                }
            }
        }

def parse_parameters(parameters):
    """Parse parameters array into a dictionary"""
    param_dict = {}
    for param in parameters:
        name = param.get('name')
        value = param.get('value')
        param_type = param.get('type')
        
        if name and value is not None:
            # Handle different parameter types
            if param_type == 'string':
                # Check if it's a comma-separated list that should be an array
                if name in ['colorScheme', 'descriptions'] and ',' in str(value):
                    param_dict[name] = [item.strip() for item in str(value).split(',')]
                else:
                    param_dict[name] = str(value)
            elif param_type == 'array':
                # Handle array types
                if isinstance(value, list):
                    param_dict[name] = value
                else:
                    # Try to parse as JSON array or comma-separated
                    try:
                        param_dict[name] = json.loads(value) if isinstance(value, str) else [str(value)]
                    except:
                        param_dict[name] = [item.strip() for item in str(value).split(',')]
            else:
                param_dict[name] = value
    
    return param_dict

def handle_generate_creative_image(action, function, message_version, service, parameters):
    """Handle image generation request - returns content IDs immediately"""
    try:
        # Parse the parameters array into a dictionary
        parsed_params = parse_parameters(parameters)
        
        # Extract color scheme (handle both single string and array formats)
        color_scheme = parsed_params.get('colorScheme', [])
        if isinstance(color_scheme, str):
            color_scheme = [item.strip() for item in color_scheme.split(',')]
        color_scheme_text = ", ".join(color_scheme) if color_scheme else ""
        
        # Extract descriptions (handle both single description and descriptions array)
        prompts = parsed_params.get('descriptions', [])
        if not prompts:
            # Fallback to single description parameter
            single_desc = parsed_params.get('description', '')
            if single_desc:
                prompts = [single_desc]
        
        if not prompts:
            return {
                'statusCode': 400,
                'body': {
                    'application/json': {
                        'body': json.dumps({
                            'error': 'Descriptions are required'
                        })
                    }
                }
            }
        
        width = int(parsed_params.get('width', 1024))
        height = int(parsed_params.get('height', 1024))
        
        # Prepare batch of image jobs for async processing
        image_jobs = []
        pending_records = []
        
        for prompt in prompts:
            # Add color scheme to prompt if available
            if color_scheme_text:
                enhanced_prompt = f"{prompt}, color scheme: {color_scheme_text}"
            else:
                enhanced_prompt = prompt
            
            # Generate unique identifiers
            image_id = str(uuid.uuid4())
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            # Generate S3 keys
            full_image_key = f"imagery/{timestamp}_{image_id}_full.jpg"
            thumbnail_key = f"imagery/{timestamp}_{image_id}_thumb.jpg"
            
            # Create pending record in DynamoDB
            pending_item = service.create_pending_record(
                content_id=image_id,
                prompt=enhanced_prompt,
                full_image_key=full_image_key,
                thumbnail_key=thumbnail_key
            )
            
            # Create job for async processor
            image_job = {
                'content_id': image_id,
                'prompt': enhanced_prompt,
                'width': width,
                'height': height,
                'full_image_key': full_image_key,
                'thumbnail_key': thumbnail_key
            }
            
            image_jobs.append(image_job)
            pending_records.append(pending_item)
        
        # Invoke async processor for all jobs
        service.invoke_async_processor(image_jobs)
        
        logger.info(f"Created {len(pending_records)} pending image records and queued for async processing")

        # refer to: https://docs.aws.amazon.com/bedrock/latest/userguide/agents-lambda.html
        response_body = {
            'TEXT': {
                'body': json.dumps(pending_records)
            }
        }
        action_response = {
            'actionGroup': action,
            'function': function,
            'functionResponse': {
                'responseBody': response_body
            }
        }
        response = {
            'response': action_response,
            'messageVersion': message_version
        }

        logger.info('Response: %s', response)
        return response

    except KeyError as e:
        logger.error('Missing required field: %s', str(e))
        return {
            'statusCode': HTTPStatus.BAD_REQUEST,
            'body': f'Error: {str(e)}'
        }
    except Exception as e:
        logger.error('Unexpected error: %s', str(e))
        return {
            'statusCode': HTTPStatus.INTERNAL_SERVER_ERROR,
            'body': 'Internal server error'
        }
