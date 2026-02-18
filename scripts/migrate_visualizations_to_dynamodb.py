#!/usr/bin/env python3
"""
Data migration script for visualization templates and agent mappings.
Migrates data from JSON files to DynamoDB table for the Visualizations action group.
"""

import json
import os
import glob
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import logging

try:
    import boto3
    from botocore.exceptions import ClientError, BotoCoreError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class VisualizationMigrator:
    """Handles migration of visualization data from files to DynamoDB format."""
    
    def __init__(self, templates_dir: str = "synthetic_data/visual-templates/templates",
                 mappings_dir: str = "synthetic_data/visual-templates/agent-mappings",
                 table_name: Optional[str] = None,
                 aws_region: str = "us-east-1"):
        """
        Initialize the migrator with source directories.
        
        Args:
            templates_dir: Directory containing template JSON files
            mappings_dir: Directory containing agent mapping JSON files
            table_name: DynamoDB table name (if None, will simulate operations)
            aws_region: AWS region for DynamoDB operations
        """
        self.templates_dir = templates_dir
        self.mappings_dir = mappings_dir
        self.table_name = table_name
        self.aws_region = aws_region
        self.records = []
        self.timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        
        # Initialize DynamoDB client if available and table name provided
        self.dynamodb = None
        self.table = None
        if BOTO3_AVAILABLE and table_name:
            try:
                self.dynamodb = boto3.resource('dynamodb', region_name=aws_region)
                self.table = self.dynamodb.Table(table_name)
                logger.info(f"Connected to DynamoDB table: {table_name}")
            except Exception as e:
                logger.warning(f"Could not connect to DynamoDB: {e}")
                self.dynamodb = None
                self.table = None
        
    def read_template_files(self) -> List[Dict[str, Any]]:
        """
        Read all template files from the templates directory.
        
        Returns:
            List of template data dictionaries
        """
        templates = []
        template_pattern = os.path.join(self.templates_dir, "*.json")
        template_files = glob.glob(template_pattern)
        
        logger.info(f"Found {len(template_files)} template files")
        
        for file_path in template_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    template_data = json.load(f)
                    templates.append(template_data)
                    logger.debug(f"Loaded template: {template_data.get('templateId', 'unknown')}")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error reading template file {file_path}: {e}")
                continue
                
        return templates
    
    def read_agent_mapping_files(self) -> List[Dict[str, Any]]:
        """
        Read all agent mapping files from the mappings directory.
        Reads both the main agent files and the detailed template mappings.
        
        Returns:
            List of agent mapping data dictionaries
        """
        mappings = []
        
        # Read the main agent files from agent-maps subdirectory
        main_mapping_pattern = os.path.join(self.mappings_dir, "agent-maps/*.json")
        main_mapping_files = glob.glob(main_mapping_pattern)
        
        logger.info(f"Found {len(main_mapping_files)} main agent mapping files")
        
        for file_path in main_mapping_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    mapping_data = json.load(f)
                    mappings.append(mapping_data)
                    logger.debug(f"Loaded main mapping: {mapping_data.get('agentName', 'unknown')}")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error reading main mapping file {file_path}: {e}")
                continue
        
        # Read the detailed template mapping files (agent-template specific)
        detailed_mapping_pattern = os.path.join(self.mappings_dir, "*-*-visualization.json")
        detailed_mapping_files = glob.glob(detailed_mapping_pattern)
        
        logger.info(f"Found {len(detailed_mapping_files)} detailed template mapping files")
        
        for file_path in detailed_mapping_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    mapping_data = json.load(f)
                    mappings.append(mapping_data)
                    logger.debug(f"Loaded detailed mapping: {mapping_data.get('agentName', 'unknown')}-{mapping_data.get('templateId', 'unknown')}")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error reading detailed mapping file {file_path}: {e}")
                continue
                
        return mappings
    
    def transform_template_to_record(self, template_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform template data into DynamoDB record format.
        
        Args:
            template_data: Raw template data from JSON file
            
        Returns:
            DynamoDB record dictionary
        """
        template_id = template_data.get('templateId')
        if not template_id:
            raise ValueError("Template missing templateId")
            
        return {
            'item_type': 'template',
            'item_id': template_id,
            'template_id': template_id,
            'data': template_data,
            'created_at': self.timestamp,
            'updated_at': self.timestamp
        }
    
    def transform_mapping_to_record(self, mapping_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform agent mapping data into DynamoDB record format.
        Handles both main agent mappings and detailed template mappings.
        
        Args:
            mapping_data: Raw agent mapping data from JSON file
            
        Returns:
            DynamoDB record dictionary
        """
        agent_name = mapping_data.get('agentName')
        if not agent_name:
            raise ValueError("Agent mapping missing agentName")
        
        # Check if this is a detailed template mapping (has templateId and dataMapping)
        template_id = mapping_data.get('templateId')
        if template_id and 'dataMapping' in mapping_data:
            # This is a detailed template mapping - create agent_template_mapping record
            item_id = f"{agent_name}#{template_id}"
            return {
                'item_type': 'agent_template_mapping',
                'item_id': item_id,
                'agent_id': agent_name,
                'template_id': template_id,
                'usage': mapping_data.get('usage', ''),
                'data': mapping_data,
                'created_at': self.timestamp,
                'updated_at': self.timestamp
            }
        elif 'templates' in mapping_data:
            # This is a main agent mapping - create agent_mapping record
            return {
                'item_type': 'agent_mapping',
                'item_id': agent_name,
                'agent_id': agent_name,
                'data': mapping_data,
                'created_at': self.timestamp,
                'updated_at': self.timestamp
            }
        else:
            # Unknown format
            raise ValueError(f"Unknown agent mapping format for {agent_name}: missing both 'dataMapping' and 'templates' fields")
    
    def create_cross_reference_records(self, mapping_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Create cross-reference records for efficient querying.
        
        Args:
            mapping_data: Agent mapping data
            
        Returns:
            List of cross-reference records
        """
        cross_refs = []
        agent_name = mapping_data.get('agentName')
        templates = mapping_data.get('templates', [])
        
        for template in templates:
            template_id = template.get('templateId')
            usage = template.get('usage', '')
            
            if template_id:
                cross_ref = {
                    'item_type': 'agent_template_ref',
                    'item_id': f"{agent_name}#{template_id}",
                    'agent_id': agent_name,
                    'template_id': template_id,
                    'usage': usage,
                    'created_at': self.timestamp,
                    'updated_at': self.timestamp
                }
                cross_refs.append(cross_ref)
                
        return cross_refs
    
    def process_templates(self) -> None:
        """Process all template files and create DynamoDB records."""
        logger.info("Processing template files...")
        templates = self.read_template_files()
        
        for template_data in templates:
            try:
                record = self.transform_template_to_record(template_data)
                self.records.append(record)
                logger.debug(f"Created template record: {record['item_id']}")
            except ValueError as e:
                logger.error(f"Error processing template: {e}")
                continue
                
        logger.info(f"Processed {len(templates)} template files")
    
    def process_agent_mappings(self) -> None:
        """Process all agent mapping files and create DynamoDB records."""
        logger.info("Processing agent mapping files...")
        mappings = self.read_agent_mapping_files()
        
        for mapping_data in mappings:
            try:
                # Create main agent mapping record
                record = self.transform_mapping_to_record(mapping_data)
                self.records.append(record)
                logger.debug(f"Created agent mapping record: {record['item_id']}")
                
                # Create cross-reference records
                cross_refs = self.create_cross_reference_records(mapping_data)
                self.records.extend(cross_refs)
                logger.debug(f"Created {len(cross_refs)} cross-reference records for {mapping_data.get('agentName')}")
                
            except ValueError as e:
                logger.error(f"Error processing agent mapping: {e}")
                continue
                
        logger.info(f"Processed {len(mappings)} agent mapping files")
    
    def _validate_item_for_write(self, item: Dict[str, Any]) -> bool:
        """
        Validate a single item before writing to DynamoDB.
        
        Args:
            item: Item to validate
            
        Returns:
            True if item is valid for DynamoDB write, False otherwise
        """
        # Check required fields
        if not item.get('item_type'):
            logger.error(f"Item missing item_type: {item.get('item_id', 'unknown')}")
            return False
            
        if not item.get('item_id'):
            logger.error(f"Item missing item_id: {item}")
            return False
        
        # Check for empty or None values in required fields
        if not isinstance(item.get('item_type'), str) or not item['item_type'].strip():
            logger.error(f"Invalid item_type for item {item.get('item_id')}: {item.get('item_type')}")
            return False
            
        if not isinstance(item.get('item_id'), str) or not item['item_id'].strip():
            logger.error(f"Invalid item_id: {item.get('item_id')}")
            return False
        
        # Validate specific record types
        if item['item_type'] == 'template':
            if not item.get('template_id'):
                logger.error(f"Template record missing template_id: {item.get('item_id')}")
                return False
            if not item.get('data'):
                logger.error(f"Template record missing data: {item.get('item_id')}")
                return False
                
        elif item['item_type'] == 'agent_mapping':
            if not item.get('agent_id'):
                logger.error(f"Agent mapping record missing agent_id: {item.get('item_id')}")
                return False
            if not item.get('data'):
                logger.error(f"Agent mapping record missing data: {item.get('item_id')}")
                return False
                
        elif item['item_type'] == 'agent_template_mapping':
            if not item.get('agent_id') or not item.get('template_id'):
                logger.error(f"Agent template mapping record missing agent_id or template_id: {item.get('item_id')}")
                return False
            if not item.get('data'):
                logger.error(f"Agent template mapping record missing data: {item.get('item_id')}")
                return False
                
        elif item['item_type'] == 'agent_template_ref':
            if not item.get('agent_id') or not item.get('template_id'):
                logger.error(f"Cross-reference record missing agent_id or template_id: {item.get('item_id')}")
                return False
        else:
            logger.error(f"Unknown item_type: {item.get('item_type')} for item {item.get('item_id')}")
            return False
        
        # Validate timestamps
        if not item.get('created_at') or not item.get('updated_at'):
            logger.error(f"Item missing timestamps: {item.get('item_id')}")
            return False
        
        return True
    
    def validate_records(self) -> bool:
        """
        Validate all records before migration with enhanced validation logic.
        
        Returns:
            True if all records are valid, False otherwise
        """
        logger.info("Validating records...")
        valid = True
        validation_errors = []
        
        # Track record types and IDs for duplicate detection
        seen_items = set()
        record_type_counts = {'template': 0, 'agent_mapping': 0, 'agent_template_mapping': 0, 'agent_template_ref': 0}
        
        for i, record in enumerate(self.records):
            record_id = record.get('item_id', f'record_{i}')
            
            # Use the enhanced validation method
            if not self._validate_item_for_write(record):
                valid = False
                validation_errors.append(f"Record {record_id} failed validation")
                continue
            
            # Check for duplicates
            item_key = (record['item_type'], record['item_id'])
            if item_key in seen_items:
                logger.error(f"Duplicate record found: {record['item_type']}#{record['item_id']}")
                valid = False
                validation_errors.append(f"Duplicate record: {record_id}")
                continue
            
            seen_items.add(item_key)
            
            # Count record types
            if record['item_type'] in record_type_counts:
                record_type_counts[record['item_type']] += 1
            
            # Additional validation for data integrity
            if record['item_type'] == 'template':
                data = record.get('data', {})
                if not data.get('templateId'):
                    logger.error(f"Template data missing templateId: {record_id}")
                    valid = False
                    validation_errors.append(f"Template {record_id} missing templateId in data")
                elif data['templateId'] != record['template_id']:
                    logger.error(f"Template ID mismatch: record={record['template_id']}, data={data['templateId']}")
                    valid = False
                    validation_errors.append(f"Template {record_id} ID mismatch")
            
            elif record['item_type'] == 'agent_mapping':
                data = record.get('data', {})
                if not data.get('agentName'):
                    logger.error(f"Agent mapping data missing agentName: {record_id}")
                    valid = False
                    validation_errors.append(f"Agent mapping {record_id} missing agentName in data")
                elif data['agentName'] != record['agent_id']:
                    logger.error(f"Agent ID mismatch: record={record['agent_id']}, data={data['agentName']}")
                    valid = False
                    validation_errors.append(f"Agent mapping {record_id} ID mismatch")
        
        # Log validation summary
        logger.info(f"Validation summary:")
        logger.info(f"  - Total records: {len(self.records)}")
        logger.info(f"  - Template records: {record_type_counts['template']}")
        logger.info(f"  - Agent mapping records: {record_type_counts['agent_mapping']}")
        logger.info(f"  - Agent template mapping records: {record_type_counts['agent_template_mapping']}")
        logger.info(f"  - Cross-reference records: {record_type_counts['agent_template_ref']}")
        
        if valid:
            logger.info(f"✅ All {len(self.records)} records passed validation")
        else:
            logger.error(f"❌ Validation failed with {len(validation_errors)} errors:")
            for error in validation_errors[:10]:  # Show first 10 errors
                logger.error(f"  - {error}")
            if len(validation_errors) > 10:
                logger.error(f"  ... and {len(validation_errors) - 10} more errors")
            
        return valid
    
    def get_migration_records(self) -> List[Dict[str, Any]]:
        """
        Get all migration records ready for DynamoDB.
        
        Returns:
            List of DynamoDB records
        """
        return self.records
    
    def get_migration_summary(self) -> Dict[str, Any]:
        """
        Get comprehensive summary of migration data with detailed statistics.
        
        Returns:
            Summary dictionary with counts and statistics
        """
        template_records = [r for r in self.records if r['item_type'] == 'template']
        agent_mapping_records = [r for r in self.records if r['item_type'] == 'agent_mapping']
        agent_template_mapping_records = [r for r in self.records if r['item_type'] == 'agent_template_mapping']
        cross_ref_records = [r for r in self.records if r['item_type'] == 'agent_template_ref']
        
        # Analyze template types
        template_types = {}
        for record in template_records:
            template_id = record.get('template_id', 'unknown')
            template_types[template_id] = template_types.get(template_id, 0) + 1
        
        # Analyze agent mappings
        agent_template_counts = {}
        for record in agent_mapping_records:
            agent_id = record.get('agent_id', 'unknown')
            data = record.get('data', {})
            templates = data.get('templates', [])
            agent_template_counts[agent_id] = len(templates)
        
        # Calculate cross-reference statistics
        cross_ref_by_agent = {}
        cross_ref_by_template = {}
        for record in cross_ref_records:
            agent_id = record.get('agent_id', 'unknown')
            template_id = record.get('template_id', 'unknown')
            
            cross_ref_by_agent[agent_id] = cross_ref_by_agent.get(agent_id, 0) + 1
            cross_ref_by_template[template_id] = cross_ref_by_template.get(template_id, 0) + 1
        
        return {
            'total_records': len(self.records),
            'template_records': len(template_records),
            'agent_mapping_records': len(agent_mapping_records),
            'agent_template_mapping_records': len(agent_template_mapping_records),
            'cross_reference_records': len(cross_ref_records),
            'timestamp': self.timestamp,
            'template_types': list(template_types.keys()),
            'template_type_count': len(template_types),
            'agent_ids': list(agent_template_counts.keys()),
            'agent_count': len(agent_template_counts),
            'avg_templates_per_agent': sum(agent_template_counts.values()) / len(agent_template_counts) if agent_template_counts else 0,
            'max_templates_per_agent': max(agent_template_counts.values()) if agent_template_counts else 0,
            'min_templates_per_agent': min(agent_template_counts.values()) if agent_template_counts else 0,
            'cross_ref_agents': len(cross_ref_by_agent),
            'cross_ref_templates': len(cross_ref_by_template),
            'most_used_template': max(cross_ref_by_template.items(), key=lambda x: x[1])[0] if cross_ref_by_template else None,
            'agent_with_most_templates': max(cross_ref_by_agent.items(), key=lambda x: x[1])[0] if cross_ref_by_agent else None
        }
    
    def print_detailed_summary(self) -> None:
        """Print a detailed, formatted summary of the migration."""
        summary = self.get_migration_summary()
        
        print("\n" + "="*60)
        print("📊 MIGRATION SUMMARY")
        print("="*60)
        print(f"🕐 Timestamp: {summary['timestamp']}")
        print(f"📝 Total Records: {summary['total_records']}")
        print()
        
        print("📋 Record Breakdown:")
        print(f"   • Template Records: {summary['template_records']}")
        print(f"   • Agent Mapping Records: {summary['agent_mapping_records']}")
        print(f"   • Agent Template Mapping Records: {summary['agent_template_mapping_records']}")
        print(f"   • Cross-Reference Records: {summary['cross_reference_records']}")
        print()
        
        print("🎨 Template Analysis:")
        print(f"   • Unique Template Types: {summary['template_type_count']}")
        if summary['template_types']:
            print(f"   • Template IDs: {', '.join(summary['template_types'][:5])}")
            if len(summary['template_types']) > 5:
                print(f"     ... and {len(summary['template_types']) - 5} more")
        print()
        
        print("🤖 Agent Analysis:")
        print(f"   • Total Agents: {summary['agent_count']}")
        print(f"   • Avg Templates per Agent: {summary['avg_templates_per_agent']:.1f}")
        print(f"   • Max Templates per Agent: {summary['max_templates_per_agent']}")
        print(f"   • Min Templates per Agent: {summary['min_templates_per_agent']}")
        if summary['agent_with_most_templates']:
            print(f"   • Agent with Most Templates: {summary['agent_with_most_templates']}")
        print()
        
        print("🔗 Cross-Reference Analysis:")
        print(f"   • Agents with Templates: {summary['cross_ref_agents']}")
        print(f"   • Templates with Agents: {summary['cross_ref_templates']}")
        if summary['most_used_template']:
            print(f"   • Most Used Template: {summary['most_used_template']}")
        
        print("="*60)
    
    def batch_write_with_retry(self, items: List[Dict[str, Any]], max_retries: int = 3) -> bool:
        """
        Write items to DynamoDB with retry logic, exponential backoff, and detailed progress tracking.
        
        Args:
            items: List of items to write
            max_retries: Maximum number of retry attempts
            
        Returns:
            True if all items were written successfully, False otherwise
        """
        if not self.table:
            logger.warning("No DynamoDB table available, simulating batch write")
            return True
            
        if not items:
            logger.warning("No items provided for batch write")
            return True
            
        # DynamoDB batch_writer handles up to 25 items per batch
        batch_size = 25
        total_items = len(items)
        written_items = 0
        failed_items = []
        start_time = time.time()
        
        logger.info(f"Starting batch write operation: {total_items} items in {(total_items + batch_size - 1) // batch_size} batches")
        
        for i in range(0, total_items, batch_size):
            batch = items[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_items + batch_size - 1) // batch_size
            retry_count = 0
            batch_start_time = time.time()
            
            logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} items)")
            
            while retry_count <= max_retries:
                try:
                    # Validate batch items before writing
                    for item in batch:
                        if not self._validate_item_for_write(item):
                            logger.error(f"Invalid item in batch {batch_num}: {item.get('item_id', 'unknown')}")
                            failed_items.extend(batch)
                            break
                    else:
                        # All items in batch are valid, proceed with write
                        with self.table.batch_writer() as batch_writer:
                            for item in batch:
                                batch_writer.put_item(Item=item)
                        
                        written_items += len(batch)
                        batch_duration = time.time() - batch_start_time
                        progress_pct = (written_items / total_items) * 100
                        
                        logger.info(f"✅ Batch {batch_num}/{total_batches} completed in {batch_duration:.2f}s "
                                  f"({progress_pct:.1f}% total progress, {written_items}/{total_items} items)")
                        break
                    
                except ClientError as e:
                    retry_count += 1
                    error_code = e.response['Error']['Code']
                    error_message = e.response['Error'].get('Message', 'Unknown error')
                    
                    if retry_count > max_retries:
                        logger.error(f"❌ Batch {batch_num} failed after {max_retries} retries: {error_code} - {error_message}")
                        failed_items.extend(batch)
                        break
                    
                    # Exponential backoff with jitter
                    base_wait = 2 ** retry_count
                    jitter = retry_count * 0.1
                    wait_time = base_wait + jitter
                    
                    logger.warning(f"⚠️  Batch {batch_num} failed ({error_code}), retrying in {wait_time:.1f}s "
                                 f"(attempt {retry_count}/{max_retries})")
                    time.sleep(wait_time)
                    
                except Exception as e:
                    logger.error(f"❌ Unexpected error in batch {batch_num}: {str(e)}")
                    failed_items.extend(batch)
                    break
        
        total_duration = time.time() - start_time
        success_rate = (written_items / total_items) * 100 if total_items > 0 else 0
        
        if failed_items:
            logger.error(f"❌ Batch write completed with errors: {len(failed_items)} items failed")
            logger.error(f"Failed items: {[item.get('item_id', 'unknown') for item in failed_items[:10]]}")
            if len(failed_items) > 10:
                logger.error(f"... and {len(failed_items) - 10} more failed items")
            return False
        else:
            logger.info(f"✅ Batch write completed successfully: {written_items} items written in {total_duration:.2f}s "
                       f"({success_rate:.1f}% success rate)")
            return True
    
    def validate_dynamodb_data(self, sample_size: int = 10) -> bool:
        """
        Comprehensive validation that data was written correctly to DynamoDB.
        
        Args:
            sample_size: Number of records to sample for detailed validation
            
        Returns:
            True if validation passes, False otherwise
        """
        if not self.table:
            logger.warning("No DynamoDB table available, skipping validation")
            return True
            
        try:
            logger.info("Starting comprehensive DynamoDB data validation...")
            
            # Get record counts by type
            template_records = [r for r in self.records if r['item_type'] == 'template']
            agent_records = [r for r in self.records if r['item_type'] == 'agent_mapping']
            agent_template_records = [r for r in self.records if r['item_type'] == 'agent_template_mapping']
            ref_records = [r for r in self.records if r['item_type'] == 'agent_template_ref']
            
            logger.info(f"Expected records: {len(template_records)} templates, "
                       f"{len(agent_records)} agent mappings, {len(agent_template_records)} agent template mappings, "
                       f"{len(ref_records)} cross-references")
            
            # Validate record counts in DynamoDB
            if not self._validate_record_counts(template_records, agent_records, agent_template_records, ref_records):
                return False
            
            # Sample records for detailed validation
            validation_records = []
            
            # Ensure we sample from each type proportionally
            template_sample_size = min(sample_size // 4, len(template_records))
            agent_sample_size = min(sample_size // 4, len(agent_records))
            agent_template_sample_size = min(sample_size // 4, len(agent_template_records))
            ref_sample_size = min(sample_size - template_sample_size - agent_sample_size - agent_template_sample_size, len(ref_records))
            
            if template_records:
                validation_records.extend(template_records[:template_sample_size])
            if agent_records:
                validation_records.extend(agent_records[:agent_sample_size])
            if agent_template_records:
                validation_records.extend(agent_template_records[:agent_template_sample_size])
            if ref_records:
                validation_records.extend(ref_records[:ref_sample_size])
            
            logger.info(f"Performing detailed validation on {len(validation_records)} sample records")
            
            # Validate each sample record
            validation_errors = []
            for i, record in enumerate(validation_records):
                try:
                    if not self._validate_single_record_in_db(record):
                        validation_errors.append(f"Record {record['item_type']}#{record['item_id']} failed validation")
                    
                    # Progress logging for large samples
                    if (i + 1) % 5 == 0:
                        logger.debug(f"Validated {i + 1}/{len(validation_records)} sample records")
                        
                except Exception as e:
                    logger.error(f"Error validating record {record.get('item_id', 'unknown')}: {e}")
                    validation_errors.append(f"Validation error for {record.get('item_id', 'unknown')}: {str(e)}")
            
            if validation_errors:
                logger.error(f"❌ Data validation failed with {len(validation_errors)} errors:")
                for error in validation_errors[:5]:  # Show first 5 errors
                    logger.error(f"  - {error}")
                if len(validation_errors) > 5:
                    logger.error(f"  ... and {len(validation_errors) - 5} more errors")
                return False
            
            # Test GSI queries to ensure indexes are working
            if not self._validate_gsi_functionality():
                return False
            
            logger.info("✅ Comprehensive DynamoDB data validation completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Data validation failed with exception: {e}")
            return False
    
    def _validate_record_counts(self, template_records: List[Dict], agent_records: List[Dict], agent_template_records: List[Dict], ref_records: List[Dict]) -> bool:
        """
        Validate that the correct number of records exist in DynamoDB for each type.
        
        Returns:
            True if counts match expectations, False otherwise
        """
        try:
            # Count records by type using scan with filter
            for record_type, expected_count, record_list in [
                ('template', len(template_records), template_records),
                ('agent_mapping', len(agent_records), agent_records),
                ('agent_template_mapping', len(agent_template_records), agent_template_records),
                ('agent_template_ref', len(ref_records), ref_records)
            ]:
                if expected_count == 0:
                    continue
                    
                response = self.table.scan(
                    FilterExpression='item_type = :item_type',
                    ExpressionAttributeValues={':item_type': record_type},
                    Select='COUNT'
                )
                
                actual_count = response.get('Count', 0)
                
                if actual_count != expected_count:
                    logger.error(f"❌ Record count mismatch for {record_type}: "
                               f"expected {expected_count}, found {actual_count}")
                    return False
                
                logger.info(f"✅ Record count verified for {record_type}: {actual_count} records")
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating record counts: {e}")
            return False
    
    def _validate_single_record_in_db(self, record: Dict[str, Any]) -> bool:
        """
        Validate a single record exists in DynamoDB with correct data.
        
        Args:
            record: Record to validate
            
        Returns:
            True if record is valid in DB, False otherwise
        """
        try:
            response = self.table.get_item(
                Key={
                    'item_type': record['item_type'],
                    'item_id': record['item_id']
                }
            )
            
            if 'Item' not in response:
                logger.error(f"Record not found in DynamoDB: {record['item_type']}#{record['item_id']}")
                return False
            
            db_item = response['Item']
            
            # Validate key fields match
            if db_item.get('item_type') != record['item_type']:
                logger.error(f"item_type mismatch for {record['item_id']}: "
                           f"expected {record['item_type']}, got {db_item.get('item_type')}")
                return False
            
            if db_item.get('item_id') != record['item_id']:
                logger.error(f"item_id mismatch for {record['item_id']}: "
                           f"expected {record['item_id']}, got {db_item.get('item_id')}")
                return False
            
            # Validate type-specific fields
            if record['item_type'] == 'template':
                if db_item.get('template_id') != record.get('template_id'):
                    logger.error(f"template_id mismatch for {record['item_id']}")
                    return False
                    
            elif record['item_type'] == 'agent_mapping':
                if db_item.get('agent_id') != record.get('agent_id'):
                    logger.error(f"agent_id mismatch for {record['item_id']}")
                    return False
                    
            elif record['item_type'] == 'agent_template_mapping':
                if (db_item.get('agent_id') != record.get('agent_id') or 
                    db_item.get('template_id') != record.get('template_id')):
                    logger.error(f"Agent template mapping field mismatch for {record['item_id']}")
                    return False
                    
            elif record['item_type'] == 'agent_template_ref':
                if (db_item.get('agent_id') != record.get('agent_id') or 
                    db_item.get('template_id') != record.get('template_id')):
                    logger.error(f"Cross-reference field mismatch for {record['item_id']}")
                    return False
            
            # Validate data field exists and is not empty
            if record['item_type'] in ['template', 'agent_mapping', 'agent_template_mapping']:
                if not db_item.get('data'):
                    logger.error(f"Missing or empty data field for {record['item_id']}")
                    return False
            
            # Validate timestamps exist
            if not db_item.get('created_at') or not db_item.get('updated_at'):
                logger.error(f"Missing timestamps for {record['item_id']}")
                return False
            
            logger.debug(f"✅ Validated record: {record['item_type']}#{record['item_id']}")
            return True
            
        except ClientError as e:
            logger.error(f"DynamoDB error validating record {record['item_id']}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error validating record {record['item_id']}: {e}")
            return False
    
    def _validate_gsi_functionality(self) -> bool:
        """
        Test that Global Secondary Indexes are working correctly.
        
        Returns:
            True if GSI queries work, False otherwise
        """
        try:
            logger.info("Testing Global Secondary Index functionality...")
            
            # Test AgentTypeIndex - find an agent with templates
            agent_records = [r for r in self.records if r['item_type'] == 'agent_mapping']
            if agent_records:
                test_agent = agent_records[0]
                agent_id = test_agent['agent_id']
                
                try:
                    response = self.table.query(
                        IndexName='AgentTypeIndex',
                        KeyConditionExpression='agent_id = :agent_id',
                        ExpressionAttributeValues={':agent_id': agent_id},
                        Limit=5
                    )
                    
                    if response.get('Items'):
                        logger.info(f"✅ AgentTypeIndex working: found {len(response['Items'])} items for agent {agent_id}")
                    else:
                        logger.warning(f"⚠️  AgentTypeIndex returned no items for agent {agent_id}")
                        
                except ClientError as e:
                    if e.response['Error']['Code'] == 'ValidationException':
                        logger.warning("⚠️  AgentTypeIndex not yet available (may still be creating)")
                    else:
                        logger.error(f"❌ AgentTypeIndex query failed: {e}")
                        return False
            
            # Test TemplateTypeIndex - find a template
            template_records = [r for r in self.records if r['item_type'] == 'template']
            if template_records:
                test_template = template_records[0]
                template_id = test_template['template_id']
                
                try:
                    response = self.table.query(
                        IndexName='TemplateTypeIndex',
                        KeyConditionExpression='template_id = :template_id',
                        ExpressionAttributeValues={':template_id': template_id},
                        Limit=5
                    )
                    
                    if response.get('Items'):
                        logger.info(f"✅ TemplateTypeIndex working: found {len(response['Items'])} items for template {template_id}")
                    else:
                        logger.warning(f"⚠️  TemplateTypeIndex returned no items for template {template_id}")
                        
                except ClientError as e:
                    if e.response['Error']['Code'] == 'ValidationException':
                        logger.warning("⚠️  TemplateTypeIndex not yet available (may still be creating)")
                    else:
                        logger.error(f"❌ TemplateTypeIndex query failed: {e}")
                        return False
            
            logger.info("✅ GSI functionality validation completed")
            return True
            
        except Exception as e:
            logger.error(f"❌ GSI validation failed: {e}")
            return False
    
    def get_table_item_count(self) -> Optional[int]:
        """
        Get the total number of items in the DynamoDB table.
        
        Returns:
            Number of items in table, or None if unable to determine
        """
        if not self.table:
            return None
            
        try:
            response = self.table.scan(Select='COUNT')
            return response.get('Count', 0)
        except Exception as e:
            logger.error(f"Error getting table item count: {e}")
            return None
    
    def write_to_dynamodb(self) -> bool:
        """
        Write all migration records to DynamoDB with comprehensive progress tracking and verification.
        
        Returns:
            True if write operation was successful, False otherwise
        """
        if not self.records:
            logger.error("❌ No records to write")
            return False
        
        start_time = time.time()
        logger.info(f"🚀 Starting DynamoDB write operation for {len(self.records)} records")
        
        # Pre-write validation
        logger.info("📋 Performing pre-write validation...")
        if not self.validate_records():
            logger.error("❌ Pre-write validation failed")
            return False
        
        # Get initial item count for verification
        initial_count = self.get_table_item_count()
        if initial_count is not None:
            logger.info(f"📊 Table currently has {initial_count} items")
        else:
            logger.warning("⚠️  Could not determine initial table item count")
        
        # Write records in batches with retry logic
        logger.info("💾 Writing records to DynamoDB...")
        write_start_time = time.time()
        success = self.batch_write_with_retry(self.records)
        write_duration = time.time() - write_start_time
        
        if not success:
            logger.error("❌ Failed to write records to DynamoDB")
            return False
        
        logger.info(f"✅ Batch write completed in {write_duration:.2f}s")
        
        # Post-write verification
        logger.info("🔍 Performing post-write verification...")
        
        # Verify item count
        final_count = self.get_table_item_count()
        if final_count is not None:
            expected_count = (initial_count or 0) + len(self.records)
            if final_count >= expected_count:
                logger.info(f"✅ Item count verification successful: table now has {final_count} items "
                           f"(expected ~{expected_count})")
            else:
                logger.error(f"❌ Item count verification failed: expected ~{expected_count} items, "
                           f"found {final_count}")
                return False
        else:
            logger.warning("⚠️  Could not verify final item count")
        
        # Comprehensive data validation
        logger.info("🔍 Performing comprehensive data validation...")
        validation_start_time = time.time()
        if not self.validate_dynamodb_data():
            logger.error("❌ Post-write data validation failed")
            return False
        
        validation_duration = time.time() - validation_start_time
        logger.info(f"✅ Data validation completed in {validation_duration:.2f}s")
        
        # Final success summary
        total_duration = time.time() - start_time
        records_per_second = len(self.records) / total_duration if total_duration > 0 else 0
        
        logger.info("🎉 DynamoDB write operation completed successfully!")
        logger.info(f"📈 Performance summary:")
        logger.info(f"   - Total records: {len(self.records)}")
        logger.info(f"   - Total duration: {total_duration:.2f}s")
        logger.info(f"   - Write duration: {write_duration:.2f}s")
        logger.info(f"   - Validation duration: {validation_duration:.2f}s")
        logger.info(f"   - Records per second: {records_per_second:.1f}")
        
        return True
    
    def migrate(self, write_to_db: bool = False) -> bool:
        """
        Execute the complete migration process with comprehensive error handling and progress tracking.
        
        Args:
            write_to_db: Whether to write data to DynamoDB (requires table configuration)
        
        Returns:
            True if migration was successful, False otherwise
        """
        migration_start_time = time.time()
        
        try:
            logger.info("🚀 Starting visualization data migration...")
            
            # Phase 1: Process templates
            logger.info("📋 Phase 1: Processing template files...")
            template_start_time = time.time()
            self.process_templates()
            template_duration = time.time() - template_start_time
            logger.info(f"✅ Template processing completed in {template_duration:.2f}s")
            
            # Phase 2: Process agent mappings
            logger.info("🤖 Phase 2: Processing agent mapping files...")
            mapping_start_time = time.time()
            self.process_agent_mappings()
            mapping_duration = time.time() - mapping_start_time
            logger.info(f"✅ Agent mapping processing completed in {mapping_duration:.2f}s")
            
            # Phase 3: Validate all records
            logger.info("🔍 Phase 3: Validating migration records...")
            validation_start_time = time.time()
            if not self.validate_records():
                logger.error("❌ Migration failed validation")
                return False
            validation_duration = time.time() - validation_start_time
            logger.info(f"✅ Record validation completed in {validation_duration:.2f}s")
            
            # Log detailed summary
            logger.info("📊 Migration preparation summary:")
            summary = self.get_migration_summary()
            logger.info(f"   - Total records prepared: {summary['total_records']}")
            logger.info(f"   - Template records: {summary['template_records']}")
            logger.info(f"   - Agent mapping records: {summary['agent_mapping_records']}")
            logger.info(f"   - Agent template mapping records: {summary['agent_template_mapping_records']}")
            logger.info(f"   - Cross-reference records: {summary['cross_reference_records']}")
            logger.info(f"   - Unique templates: {summary['template_type_count']}")
            logger.info(f"   - Unique agents: {summary['agent_count']}")
            
            # Phase 4: Write to DynamoDB if requested
            if write_to_db:
                logger.info("💾 Phase 4: Writing data to DynamoDB...")
                if not self.write_to_dynamodb():
                    logger.error("❌ Failed to write data to DynamoDB")
                    return False
                logger.info("✅ DynamoDB write operation completed successfully")
            else:
                logger.info("ℹ️  Skipping DynamoDB write (dry-run mode)")
            
            # Final summary
            total_duration = time.time() - migration_start_time
            logger.info("🎉 Migration completed successfully!")
            logger.info(f"⏱️  Total migration time: {total_duration:.2f}s")
            logger.info(f"   - Template processing: {template_duration:.2f}s")
            logger.info(f"   - Mapping processing: {mapping_duration:.2f}s")
            logger.info(f"   - Validation: {validation_duration:.2f}s")
            if write_to_db:
                db_duration = total_duration - template_duration - mapping_duration - validation_duration
                logger.info(f"   - DynamoDB operations: {db_duration:.2f}s")
            
            return True
            
        except FileNotFoundError as e:
            logger.error(f"❌ Migration failed - file not found: {e}")
            return False
        except json.JSONDecodeError as e:
            logger.error(f"❌ Migration failed - JSON parsing error: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Migration failed with unexpected error: {e}")
            logger.error(f"Error type: {type(e).__name__}")
            import traceback
            logger.debug(f"Full traceback: {traceback.format_exc()}")
            return False


def main():
    """Main function for running the migration script."""
    import argparse
    import sys
    
    # Check if running in test mode (before argparse)
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        # Import and run test function
        def run_test():
            print("Running migration test...")
            
            migrator = VisualizationMigrator()
            
            # Test data preparation
            if not migrator.migrate(write_to_db=False):
                print("❌ Migration test failed")
                return False
            
            records = migrator.get_migration_records()
            summary = migrator.get_migration_summary()
            
            # Verify record structure
            template_found = False
            agent_mapping_found = False
            agent_template_mapping_found = False
            cross_ref_found = False
            
            for record in records:
                if record['item_type'] == 'template':
                    template_found = True
                    # Verify template record structure
                    if 'template_id' not in record:
                        raise ValueError(f"Template record missing required field 'template_id': {record}")
                    if 'data' not in record:
                        raise ValueError(f"Template record missing required field 'data': {record}")
                    if 'created_at' not in record:
                        raise ValueError(f"Template record missing required field 'created_at': {record}")
                    
                elif record['item_type'] == 'agent_mapping':
                    agent_mapping_found = True
                    # Verify agent mapping record structure
                    if 'agent_id' not in record:
                        raise ValueError(f"Agent mapping record missing required field 'agent_id': {record}")
                    if 'data' not in record:
                        raise ValueError(f"Agent mapping record missing required field 'data': {record}")
                    if 'created_at' not in record:
                        raise ValueError(f"Agent mapping record missing required field 'created_at': {record}")
                    
                elif record['item_type'] == 'agent_template_mapping':
                    agent_template_mapping_found = True
                    # Verify agent template mapping record structure
                    if 'agent_id' not in record:
                        raise ValueError(f"Agent template mapping record missing required field 'agent_id': {record}")
                    if 'template_id' not in record:
                        raise ValueError(f"Agent template mapping record missing required field 'template_id': {record}")
                    if 'data' not in record:
                        raise ValueError(f"Agent template mapping record missing required field 'data': {record}")
                    if 'created_at' not in record:
                        raise ValueError(f"Agent template mapping record missing required field 'created_at': {record}")
                    
                elif record['item_type'] == 'agent_template_ref':
                    cross_ref_found = True
                    # Verify cross-reference record structure
                    if 'agent_id' not in record:
                        raise ValueError(f"Cross-reference record missing required field 'agent_id': {record}")
                    if 'template_id' not in record:
                        raise ValueError(f"Cross-reference record missing required field 'template_id': {record}")
                    if 'usage' not in record:
                        raise ValueError(f"Cross-reference record missing required field 'usage': {record}")
            
            # Verify all record types were created
            if not template_found:
                raise ValueError("No template records found")
            if not agent_mapping_found:
                raise ValueError("No agent mapping records found")
            if not agent_template_mapping_found:
                raise ValueError("No agent template mapping records found")
            if not cross_ref_found:
                raise ValueError("No cross-reference records found")
            
            print("✅ Migration test passed")
            print(f"   - {summary['template_records']} template records")
            print(f"   - {summary['agent_mapping_records']} agent mapping records")
            print(f"   - {summary['agent_template_mapping_records']} agent template mapping records")
            print(f"   - {summary['cross_reference_records']} cross-reference records")
            print(f"   - {summary['total_records']} total records")
            
            return True
        
        return run_test()
    
    parser = argparse.ArgumentParser(description='Migrate visualization data to DynamoDB')
    parser.add_argument('--table-name', help='DynamoDB table name')
    parser.add_argument('--region', default='us-east-1', help='AWS region (default: us-east-1)')
    parser.add_argument('--write-to-db', action='store_true', help='Write data to DynamoDB (requires --table-name)')
    parser.add_argument('--dry-run', action='store_true', help='Prepare data but do not write to DynamoDB')
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.write_to_db and not args.table_name:
        logger.error("--table-name is required when using --write-to-db")
        exit(1)
    
    if args.write_to_db and not BOTO3_AVAILABLE:
        logger.error("boto3 is required for DynamoDB operations. Install with: pip install boto3")
        exit(1)
    
    # Initialize migrator
    migrator = VisualizationMigrator(
        table_name=args.table_name,
        aws_region=args.region
    )
    
    # Run migration
    write_to_db = args.write_to_db and not args.dry_run
    
    if migrator.migrate(write_to_db=write_to_db):
        logger.info("✅ Migration completed successfully")
        
        # Print detailed summary
        migrator.print_detailed_summary()
        
        if write_to_db:
            print(f"\n🎉 Data successfully written to DynamoDB table: {args.table_name}")
            print(f"🔍 You can verify the data using the AWS Console or CLI")
        else:
            # Save records to file for inspection
            records = migrator.get_migration_records()
            output_file = 'migration_records.json'
            with open(output_file, 'w') as f:
                json.dump(records, f, indent=2)
            print(f"\n💾 Records saved to {output_file} for inspection")
            print(f"📁 File size: {os.path.getsize(output_file) / 1024:.1f} KB")
            
            if args.table_name:
                print(f"\n🚀 To write to DynamoDB, run:")
                print(f"   python3 {__file__} --table-name {args.table_name} --region {args.region} --write-to-db")
        
    else:
        logger.error("❌ Migration failed")
        print("\n❌ Migration failed. Check the logs above for details.")
        exit(1)


if __name__ == "__main__":
    main()


def test_migration():
    """Test function to verify migration logic without DynamoDB."""
    print("Running migration test...")
    
    migrator = VisualizationMigrator()
    
    # Test data preparation
    if not migrator.migrate(write_to_db=False):
        print("❌ Migration test failed")
        return False
    
    records = migrator.get_migration_records()
    summary = migrator.get_migration_summary()
    
    # Verify record structure
    template_found = False
    agent_mapping_found = False
    cross_ref_found = False
    
    for record in records:
        if record['item_type'] == 'template':
            template_found = True
            # Verify template record structure
            if 'template_id' not in record:
                raise ValueError(f"Template record missing required field 'template_id': {record}")
            if 'data' not in record:
                raise ValueError(f"Template record missing required field 'data': {record}")
            if 'created_at' not in record:
                raise ValueError(f"Template record missing required field 'created_at': {record}")
            
        elif record['item_type'] == 'agent_mapping':
            agent_mapping_found = True
            # Verify agent mapping record structure
            if 'agent_id' not in record:
                raise ValueError(f"Agent mapping record missing required field 'agent_id': {record}")
            if 'data' not in record:
                raise ValueError(f"Agent mapping record missing required field 'data': {record}")
            if 'created_at' not in record:
                raise ValueError(f"Agent mapping record missing required field 'created_at': {record}")
            
        elif record['item_type'] == 'agent_template_ref':
            cross_ref_found = True
            # Verify cross-reference record structure
            if 'agent_id' not in record:
                raise ValueError(f"Cross-reference record missing required field 'agent_id': {record}")
            if 'template_id' not in record:
                raise ValueError(f"Cross-reference record missing required field 'template_id': {record}")
            if 'usage' not in record:
                raise ValueError(f"Cross-reference record missing required field 'usage': {record}")
    
    # Verify all record types were created
    if not template_found:
        raise ValueError("No template records found")
    if not agent_mapping_found:
        raise ValueError("No agent mapping records found")
    if not cross_ref_found:
        raise ValueError("No cross-reference records found")
    
    # Verify counts match expected values
    if summary['template_records'] != 11:
        raise ValueError(f"Expected 11 template records, got {summary['template_records']}")
    if summary['agent_mapping_records'] != 12:
        raise ValueError(f"Expected 12 agent mapping records, got {summary['agent_mapping_records']}")
    if summary['cross_reference_records'] <= 0:
        raise ValueError("No cross-reference records created")
    
    print("✅ Migration test passed")
    print(f"   - {summary['template_records']} template records")
    print(f"   - {summary['agent_mapping_records']} agent mapping records")
    print(f"   - {summary['cross_reference_records']} cross-reference records")
    print(f"   - {summary['total_records']} total records")
    
    return True


if __name__ == "__main__":
    main()