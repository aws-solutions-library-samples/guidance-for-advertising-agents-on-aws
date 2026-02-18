"""
Action Items Tracker

Tracks action items extracted from agent responses across a session.
Provides utilities for managing and retrieving action items for the UI.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ActionItem:
    """Represents a single action item extracted from an agent response"""
    
    def __init__(
        self,
        text: str,
        agent_name: str,
        timestamp: Optional[datetime] = None,
        priority: str = "normal",
        completed: bool = False
    ):
        self.text = text
        self.agent_name = agent_name
        self.timestamp = timestamp or datetime.now()
        self.priority = priority  # "high", "normal", "low"
        self.completed = completed
        self.id = f"{agent_name}_{self.timestamp.timestamp()}_{hash(text)}"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "text": self.text,
            "agent_name": self.agent_name,
            "timestamp": self.timestamp.isoformat(),
            "priority": self.priority,
            "completed": self.completed
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActionItem':
        """Create ActionItem from dictionary"""
        item = cls(
            text=data["text"],
            agent_name=data["agent_name"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            priority=data.get("priority", "normal"),
            completed=data.get("completed", False)
        )
        item.id = data.get("id", item.id)
        return item


class ActionItemsTracker:
    """Tracks action items across a session"""
    
    def __init__(self):
        self._items: Dict[str, ActionItem] = {}
        self._session_items: Dict[str, List[str]] = {}  # session_id -> list of item IDs
    
    def add_items(
        self,
        items: List[str],
        agent_name: str,
        session_id: str,
        priority: str = "normal"
    ) -> List[ActionItem]:
        """
        Add action items for a session
        
        Args:
            items: List of action item texts
            agent_name: Name of the agent that generated the items
            session_id: Session ID to associate items with
            priority: Priority level ("high", "normal", "low")
        
        Returns:
            List of created ActionItem objects
        """
        created_items = []
        
        for item_text in items:
            if not item_text or not item_text.strip():
                continue
            
            action_item = ActionItem(
                text=item_text.strip(),
                agent_name=agent_name,
                priority=priority
            )
            
            self._items[action_item.id] = action_item
            
            # Associate with session
            if session_id not in self._session_items:
                self._session_items[session_id] = []
            self._session_items[session_id].append(action_item.id)
            
            created_items.append(action_item)
            logger.info(f"Added action item for {agent_name} in session {session_id}: {item_text[:50]}...")
        
        return created_items
    
    def get_session_items(
        self,
        session_id: str,
        include_completed: bool = True
    ) -> List[ActionItem]:
        """
        Get all action items for a session
        
        Args:
            session_id: Session ID to retrieve items for
            include_completed: Whether to include completed items
        
        Returns:
            List of ActionItem objects for the session
        """
        item_ids = self._session_items.get(session_id, [])
        items = [self._items[item_id] for item_id in item_ids if item_id in self._items]
        
        if not include_completed:
            items = [item for item in items if not item.completed]
        
        # Sort by timestamp (newest first)
        items.sort(key=lambda x: x.timestamp, reverse=True)
        
        return items
    
    def get_all_items(self, include_completed: bool = True) -> List[ActionItem]:
        """Get all action items across all sessions"""
        items = list(self._items.values())
        
        if not include_completed:
            items = [item for item in items if not item.completed]
        
        # Sort by timestamp (newest first)
        items.sort(key=lambda x: x.timestamp, reverse=True)
        
        return items
    
    def mark_completed(self, item_id: str) -> bool:
        """Mark an action item as completed"""
        if item_id in self._items:
            self._items[item_id].completed = True
            logger.info(f"Marked action item {item_id} as completed")
            return True
        return False
    
    def mark_uncompleted(self, item_id: str) -> bool:
        """Mark an action item as not completed"""
        if item_id in self._items:
            self._items[item_id].completed = False
            logger.info(f"Marked action item {item_id} as not completed")
            return True
        return False
    
    def remove_item(self, item_id: str) -> bool:
        """Remove an action item"""
        if item_id in self._items:
            # Remove from session associations
            for session_id, item_ids in self._session_items.items():
                if item_id in item_ids:
                    item_ids.remove(item_id)
            
            # Remove the item
            del self._items[item_id]
            logger.info(f"Removed action item {item_id}")
            return True
        return False
    
    def clear_session(self, session_id: str):
        """Clear all action items for a session"""
        if session_id in self._session_items:
            item_ids = self._session_items[session_id]
            for item_id in item_ids:
                if item_id in self._items:
                    del self._items[item_id]
            del self._session_items[session_id]
            logger.info(f"Cleared all action items for session {session_id}")
    
    def to_dict(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Export action items to dictionary for JSON serialization
        
        Args:
            session_id: If provided, only export items for this session
        
        Returns:
            Dictionary with action items data
        """
        if session_id:
            items = self.get_session_items(session_id)
        else:
            items = self.get_all_items()
        
        return {
            "items": [item.to_dict() for item in items],
            "total_count": len(items),
            "completed_count": sum(1 for item in items if item.completed),
            "pending_count": sum(1 for item in items if not item.completed)
        }


# Global tracker instance
_global_tracker = ActionItemsTracker()


def get_tracker() -> ActionItemsTracker:
    """Get the global action items tracker instance"""
    return _global_tracker
