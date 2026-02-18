import json
import boto3
import base64
import uuid
import os
from datetime import datetime
import logging
import random

logger = logging.getLogger()
logger.setLevel(logging.INFO)

class AsyncImageProcessor:

    def __init__(self):
        self.bedrock_client = boto3.client('bedrock-runtime')
        self.s3_client = boto3.client('s3')
        self.bucket_name = os.environ.get('GENERATED_CONTENT_BUCKET')
        self.model_id = os.environ['IMAGE_GENERATION_MODEL']
        self.table_name = os.environ['GENERATED_CONTENT_TABLE']
        self.table = boto3.resource('dynamodb').Table(self.table_name.strip())
                
    def generate_with_nova_canvas(self, prompt, width=1024, height=1024):
        try:
            # Using random for image generation seed - not security-sensitive
            seed = random.randint(0, 2147483646)
    
            request_body = {
                "taskType": "TEXT_IMAGE",
                "textToImageParams": {
                    "text": prompt,
                    "negativeText": "blurry, low quality, distorted, watermark"
                },
                "imageGenerationConfig": {
                    "numberOfImages": 1,
                    "quality": "standard",
                    "height": height,
                    "width": width,
                    "cfgScale": 7.0,
                    "seed": seed
                }
            }
            
            response = self.bedrock_client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(request_body),
                contentType='application/json',
                accept='application/json'
            )
            
            response_body = json.loads(response['body'].read())
            
            if 'images' in response_body and len(response_body['images']) > 0:
                return base64.b64decode(response_body['images'][0])
            else:
                raise Exception("No image data returned from Nova Canvas")
                
        except Exception as e:
            logger.error(f"Error generating image with Nova Canvas: {str(e)}")
            raise
        
    def generate_with_stable_diffusion(self, prompt, width=1024, height=1024):
        try:
            # Using random for image generation seed - not security-sensitive
            seed = random.randint(0, 2147483646)
    
            negative_prompt = "blurry, low quality, distorted, watermark, text overlay, signature, deformed, ugly, pixelated"
            aspect_ratio = self.calculate_aspect_ratio(width, height)
    
            request_body = {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "mode": "text-to-image",
                "aspect_ratio": aspect_ratio,
                "seed": seed,
                "output_format": "png"
            }
    
            response = self.bedrock_client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(request_body),
                contentType='application/json',
                accept='application/json'
            )
    
            response_body = json.loads(response['body'].read())
    
            # Check for filtering or errors
            if 'finish_reasons' in response_body:
                finish_reasons = response_body['finish_reasons']
                if finish_reasons and finish_reasons[0] is not None:
                    raise Exception(f"Image generation filtered: {finish_reasons[0]}")
    
            if 'images' in response_body and len(response_body['images']) > 0:
                return base64.b64decode(response_body['images'][0])
            else:
                raise Exception("No images returned from Stable Diffusion")
    
        except Exception as e:
            logger.error(f"Error generating image with Stable Diffusion: {str(e)}")
            raise
        
    def calculate_aspect_ratio(self, width, height):
        ratio = width / height
        if ratio >= 2.3:
            return "21:9"
        elif ratio >= 1.7:
            return "16:9"
        elif ratio >= 1.4:
            return "3:2"
        elif ratio >= 1.15:
            return "5:4"
        elif ratio >= 0.85:
            return "1:1"
        elif ratio >= 0.7:
            return "4:5"
        elif ratio >= 0.6:
            return "2:3"
        elif ratio >= 0.45:
            return "9:16"
        else:
            return "9:21"
    
    def generate_image(self, prompt, width=1024, height=1024):
        """Generate image using the appropriate model based on model_id"""
        try:
            logger.info(f"Generating image with model: {self.model_id}")
            
            if self.model_id == 'amazon.nova-canvas-v1:0':
                return self.generate_with_nova_canvas(prompt, width, height)
            elif 'stability' in self.model_id:
                return self.generate_with_stable_diffusion(prompt, width, height)
            else:
                raise Exception(f"Unsupported model: {self.model_id}")
                
        except Exception as e:
            logger.error(f"Error generating image: {str(e)}")
            raise
        
    def create_simple_thumbnail(self, image_data):
        # Simplified thumbnail - just return original for CloudFormation deployment
        # Real deployment will include PIL for proper thumbnails
        return image_data
                  
    def upload_to_s3(self, image_data, key, content_type='image/jpeg'):
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=image_data,
                ContentType=content_type,
                ServerSideEncryption='AES256'
            )
            return True
        except Exception as e:
            logger.error(f"Error uploading to S3: {str(e)}")
            raise
                  
    def generate_presigned_url(self, key, expiration=604800):
        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': key},
                ExpiresIn=expiration
            )
            return url
        except Exception as e:
            logger.error(f"Error generating presigned URL: {str(e)}")
            raise
        
    def update_dynamo_status(self, content_id, status, original_url=None, thumbnail_url=None, error_message=None):
        try:
            update_expression = "SET #status = :status, updated_date = :updated_date"
            expression_attribute_names = {'#status': 'status'}
            expression_attribute_values = {
                ':status': status,
                ':updated_date': datetime.now().isoformat()
            }
            
            if original_url:
                update_expression += ", original_url = :original_url"
                expression_attribute_values[':original_url'] = original_url
            
            if thumbnail_url:
                update_expression += ", thumbnail_url = :thumbnail_url"
                expression_attribute_values[':thumbnail_url'] = thumbnail_url
            
            if error_message:
                update_expression += ", error_message = :error_message"
                expression_attribute_values[':error_message'] = error_message
            
            self.table.update_item(
                Key={'content_id': content_id},
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values
            )
        except Exception as e:
            logger.error(f"Error updating DynamoDB: {str(e)}")
            raise
        
    def process_image_generation(self, image_job):
        content_id = image_job['content_id']
        prompt = image_job['prompt']
        width = image_job.get('width', 1024)
        height = image_job.get('height', 1024)
        full_image_key = image_job['full_image_key']
        thumbnail_key = image_job['thumbnail_key']
        
        try:
            self.update_dynamo_status(content_id, 'generating')
            
            image_data = self.generate_image(prompt, width, height)
            
            thumbnail_data = self.create_simple_thumbnail(image_data)
            
            self.upload_to_s3(image_data, full_image_key)
            self.upload_to_s3(thumbnail_data, thumbnail_key)
            
            full_image_url = self.generate_presigned_url(full_image_key, 604800)
            thumbnail_url = self.generate_presigned_url(thumbnail_key, 604800)
            
            self.update_dynamo_status(
                content_id, 
                'completed', 
                original_url=full_image_url,
                thumbnail_url=thumbnail_url
            )
            
        except Exception as e:
            logger.error(f"Error processing image generation for content_id {content_id}: {str(e)}")
            self.update_dynamo_status(content_id, 'failed', error_message=str(e))
            raise

def lambda_handler(event, context):
    try:
        print(event)
        processor = AsyncImageProcessor()
        image_jobs = event.get('image_jobs', [])
        
        if not image_jobs:
            return {'statusCode': 400, 'body': json.dumps({'error': 'No image jobs provided'})}
        
        results = []
        for image_job in image_jobs:
            try:
                processor.process_image_generation(image_job)
                results.append({'content_id': image_job['content_id'], 'status': 'success'})
            except Exception as e:
                results.append({'content_id': image_job['content_id'], 'status': 'failed', 'error': str(e)})
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': f'Processed {len(results)} image jobs', 'results': results})
        }
        
    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'error': f'Internal server error: {str(e)}'})}