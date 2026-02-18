import json
import boto3
import cfnresponse
import logging
import secrets
import string

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def generate_temp_password():
    # Generate a secure temporary password
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    password = ''.join(secrets.choice(alphabet) for i in range(12))
    # Ensure it meets requirements
    if not any(c.isupper() for c in password):
        password = password[:-1] + 'A'
    if not any(c.islower() for c in password):
        password = password[:-1] + 'a'
    if not any(c.isdigit() for c in password):
        password = password[:-1] + '1'
    return password

def lambda_handler(event, context):
    logger.info(f"Event: {json.dumps(event, default=str)}")
    logger.info(f"Context: {context}")
    
    try:
        user_pool_id = event['ResourceProperties']['UserPoolId']
        email = event['ResourceProperties']['DemoUserEmail']
        logger.info(f"Processing request for user pool: {user_pool_id}, email: {email}")
        
        cognito = boto3.client('cognito-idp')
        
        if event['RequestType'] == 'Delete':
            # Delete the demo user
            try:
                cognito.admin_delete_user(
                    UserPoolId=user_pool_id,
                    Username=email
                )
                logger.info(f"Deleted demo user: {email}")
            except cognito.exceptions.UserNotFoundException:
                logger.info(f"Demo user {email} not found, nothing to delete")
            except Exception as e:
                logger.warning(f"Error deleting user: {e}")
            
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            return
        
        elif event['RequestType'] in ['Create', 'Update']:
            temp_password = generate_temp_password()
            
            try:
                # Check if user already exists
                try:
                    existing_user = cognito.admin_get_user(
                        UserPoolId=user_pool_id,
                        Username=email
                    )
                    logger.info(f"User {email} already exists")
                    # Set a new temporary password for existing user
                    cognito.admin_set_user_password(
                        UserPoolId=user_pool_id,
                        Username=email,
                        Password=temp_password,
                        Permanent=False
                    )
                    logger.info(f"Updated password for existing user: {email}")
                except cognito.exceptions.UserNotFoundException:
                    # Create new user - keep it simple
                    response = cognito.admin_create_user(
                        UserPoolId=user_pool_id,
                        Username=email,
                        UserAttributes=[
                            {
                                'Name': 'email',
                                'Value': email
                            }
                        ],
                        TemporaryPassword=temp_password
                        # No MessageAction specified - use default behavior
                    )
                    logger.info(f"Created demo user: {email}")
                
                cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                    'Username': email,
                    'TemporaryPassword': temp_password,
                    'Message': f'Demo user created with email: {email}'
                })
                
            except Exception as e:
                logger.error(f"Error creating/updating user: {e}")
                cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': str(e)})
    
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        try:
            cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': str(e)})
        except Exception as send_error:
            logger.error(f"Failed to send CloudFormation response: {send_error}", exc_info=True)