import logging
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from tools.logging_config import setup_logging

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

def get_task_list_by_title(tasks_service: Resource, title: str) -> dict:
    """
    Searches for a task list with a matching title.
    
    Returns the task list resource (dict) if found, else an empty dict.
    """
    try:
        result = tasks_service.tasklists().list().execute()
        tasklists = result.get('items', [])
        for tasklist in tasklists:
            if tasklist.get('title') == title:
                logger.info("Found existing task list with title '%s'.", title)
                return tasklist
        logger.info("No task list found with title '%s'.", title)
        return {}
    except HttpError as error:
        logger.error("Error retrieving task lists: %s", error)
        return {}

def get_or_create_task_list(tasks_service: Resource, title: str) -> str:
    """
    Retrieves the task list ID for the given title. If it doesn't exist,
    a new task list is created and its ID is returned.
    """
    tasklist = get_task_list_by_title(tasks_service, title)
    if tasklist:
        return tasklist.get('id')
    # Task list not found, so create one.
    try:
        new_tasklist = tasks_service.tasklists().insert(body={"title": title}).execute()
        logger.info("Created new task list with title '%s' and id '%s'.", title, new_tasklist.get('id'))
        return new_tasklist.get('id')
    except HttpError as error:
        logger.error("Error creating new task list '%s': %s", title, error)
        return ""

# The following functions operate on tasks within a valid task list.
def clear_tasks(tasks_service: Resource, tasklist: str) -> dict:
    """
    Clears all completed tasks from the specified task list.
    
    REST call: POST /tasks/v1/lists/{tasklist}/clear
    """
    try:
        response = tasks_service.tasks().clear(tasklist=tasklist).execute()
        logger.info("Cleared completed tasks for task list '%s'.", tasklist)
        return response
    except HttpError as error:
        logger.error("Error clearing tasks on task list '%s': %s", tasklist, error)
        return {}

def delete_task(tasks_service: Resource, tasklist: str, task: str) -> None:
    """
    Deletes the specified task from the task list.
    
    REST call: DELETE /tasks/v1/lists/{tasklist}/tasks/{task}
    """
    try:
        tasks_service.tasks().delete(tasklist=tasklist, task=task).execute()
        logger.info("Deleted task '%s' from task list '%s'.", task, tasklist)
    except HttpError as error:
        logger.error("Error deleting task '%s' on task list '%s': %s", task, tasklist, error)

def get_task(tasks_service: Resource, tasklist: str, task: str) -> dict:
    """
    Returns the specified task.
    
    REST call: GET /tasks/v1/lists/{tasklist}/tasks/{task}
    """
    try:
        response = tasks_service.tasks().get(tasklist=tasklist, task=task).execute()
        logger.info("Retrieved task '%s' from task list '%s'.", task, tasklist)
        return response
    except HttpError as error:
        logger.error("Error getting task '%s' from task list '%s': %s", task, tasklist, error)
        return {}

def insert_task(tasks_service: Resource, tasklist: str, task_body: dict) -> dict:
    """
    Creates a new task on the specified task list.
    
    REST call: POST /tasks/v1/lists/{tasklist}/tasks
    The task_body parameter should be a dict containing the new task properties.
    """
    try:
        response = tasks_service.tasks().insert(tasklist=tasklist, body=task_body).execute()
        logger.info("Inserted new task on task list '%s'.", tasklist)
        return response
    except HttpError as error:
        logger.error("Error inserting task on task list '%s': %s", tasklist, error)
        return {}

def list_tasks(tasks_service: Resource, tasklist: str, **kwargs) -> dict:
    """
    Returns all tasks in the specified task list.
    
    REST call: GET /tasks/v1/lists/{tasklist}/tasks
    Optional kwargs can be passed to customize the request.
    """
    try:
        response = tasks_service.tasks().list(tasklist=tasklist, **kwargs).execute()
        logger.info("Listed tasks for task list '%s'.", tasklist)
        return response
    except HttpError as error:
        logger.error("Error listing tasks on task list '%s': %s", tasklist, error)
        return {}

def move_task(tasks_service: Resource, tasklist: str, task: str, body: dict = None) -> dict:
    """
    Moves the specified task to a new position within the task list.
    
    REST call: POST /tasks/v1/lists/{tasklist}/tasks/{task}/move
    """
    try:
        response = tasks_service.tasks().move(tasklist=tasklist, task=task, body=body or {}).execute()
        logger.info("Moved task '%s' in task list '%s'.", task, tasklist)
        return response
    except HttpError as error:
        logger.error("Error moving task '%s' on task list '%s': %s", task, tasklist, error)
        return {}

def patch_task(tasks_service: Resource, tasklist: str, task: str, updates: dict) -> dict:
    """
    Partially updates the specified task.
    
    REST call: PATCH /tasks/v1/lists/{tasklist}/tasks/{task}
    """
    try:
        response = tasks_service.tasks().patch(tasklist=tasklist, task=task, body=updates).execute()
        logger.info("Patched task '%s' on task list '%s'.", task, tasklist)
        return response
    except HttpError as error:
        logger.error("Error patching task '%s' on task list '%s': %s", task, tasklist, error)
        return {}

def update_task(tasks_service: Resource, tasklist: str, task: str, updates: dict) -> dict:
    """
    Fully updates the specified task.
    
    REST call: PUT /tasks/v1/lists/{tasklist}/tasks/{task}
    """
    try:
        response = tasks_service.tasks().update(tasklist=tasklist, task=task, body=updates).execute()
        logger.info("Updated task '%s' on task list '%s'.", task, tasklist)
        return response
    except HttpError as error:
        logger.error("Error updating task '%s' on task list '%s': %s", task, tasklist, error)
        return {}
