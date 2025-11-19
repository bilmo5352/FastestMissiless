#!/usr/bin/env python3
"""
Railway Container Restart Script
Automatically fetches the latest deployment ID from project, environment, and service IDs,
then restarts that deployment via Railway API.
"""

import requests
import time
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RAILWAY-RESTART] %(levelname)s: %(message)s"
)

# Configuration - Replace with your actual IDs and token
RAILWAY_API_TOKEN = "d95e5bc1-6f36-4bcb-a865-70404110ddf3"  # Railway API token
RAILWAY_API_URL = "https://backboard.railway.app/graphql/v2"

# The project, environment, and service IDs to fetch deployment for
PROJECT_ID = "4edb87a8-7e20-4e90-a910-c1d54af49e0a"
ENVIRONMENT_ID = "db724d7b-ca8a-455a-965a-c2c32ed6220c"
SERVICE_ID = "9382550d-ed11-44b1-be35-f12854392b0e"

# Stagger restarts by this many minutes (can set 0 if only one service)
STAGGER_MINUTES = 0


def get_latest_deployment_id():
    """Query Railway API to get the latest deployment ID for specified project/env/service."""
    headers = {
        "Authorization": f"Bearer {RAILWAY_API_TOKEN}",
        "Content-Type": "application/json"
    }

    query = """
    query deployments {
      deployments(
        first: 1
        input: {
          projectId: "%s",
          environmentId: "%s",
          serviceId: "%s"
        }
      ) {
        edges {
          node {
            id
            staticUrl
          }
        }
      }
    }
    """ % (PROJECT_ID, ENVIRONMENT_ID, SERVICE_ID)

    try:
        response = requests.post(
            RAILWAY_API_URL,
            headers=headers,
            json={"query": query},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        logging.debug(f"Deployment query response: {data}")

        deployments = data.get('data', {}).get('deployments', {}).get('edges', [])
        if deployments:
            deployment_id = deployments[0]['node']['id']
            logging.info(f"Latest deployment ID: {deployment_id}")
            return deployment_id
        else:
            logging.error("No deployments found for specified project/environment/service")
            return None
    except Exception as e:
        logging.error(f"Error fetching latest deployment ID: {e}")
        return None


def restart_deployment(deployment_id):
    """Restart a deployment by ID using Railway's official mutation."""
    headers = {
        "Authorization": f"Bearer {RAILWAY_API_TOKEN}",
        "Content-Type": "application/json"
    }

    mutation = f"""
    mutation deploymentRestart {{
      deploymentRestart(id: "{deployment_id}")
    }}
    """

    try:
        response = requests.post(
            RAILWAY_API_URL,
            headers=headers,
            json={"query": mutation},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()

        logging.debug(f"Restart mutation response: {data}")

        if 'errors' in data:
            logging.error(f"GraphQL errors: {data['errors']}")
            return False

        if 'data' in data and data['data'].get('deploymentRestart') is True:
            logging.info(f"Successfully restarted deployment {deployment_id}")
            return True
        else:
            logging.error(f"Unexpected response: {data}")
            return False
    except Exception as e:
        logging.error(f"Error restarting deployment: {e}")
        return False


def restart_service():
    """Fetch latest deployment ID and restart it."""
    deployment_id = get_latest_deployment_id()
    if not deployment_id:
        logging.error("Cannot restart: deployment ID not found")
        return False

    return restart_deployment(deployment_id)


def main():
    logging.info("Starting Railway deployment restart script...")

    success = restart_service()

    if success:
        logging.info("Deployment restart successful.")
    else:
        logging.error("Deployment restart failed.")

    logging.info("Script completed.")


if __name__ == "__main__":
    if RAILWAY_API_TOKEN == "YOUR_API_TOKEN_HERE" or not RAILWAY_API_TOKEN:
        print("ERROR: Please set RAILWAY_API_TOKEN in the script before running")
        exit(1)
    if not (PROJECT_ID and ENVIRONMENT_ID and SERVICE_ID):
        print("ERROR: Please set PROJECT_ID, ENVIRONMENT_ID, and SERVICE_ID in the script")
        exit(1)

    main()
