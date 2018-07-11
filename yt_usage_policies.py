#!/usr/bin/python

# based on YouTube Content ID code sample
# https://developers.google.com/youtube/partner/code_samples/python#create_an_asset__upload_and_claim_a_video

from google.appengine.ext import vendor
vendor.add('lib')

import os
import json
import logging

from google.oauth2 import service_account
from google.auth import app_engine
import googleapiclient.discovery
from yt_co_ids import CONTENT_OWNER_ID, POLICY_ID

# Only required when testing with local development server
# https://developers.google.com/youtube/partner/guides/oauth2_for_service_accounts#setup
SERVICE_ACCOUNT_FILE = './service_account_key.json'

YOUTUBE_SCOPES = (
  # This OAuth 2.0 access scope allows for full read/write access to the
  # authenticated user's account.
  "https://www.googleapis.com/auth/youtube",
  # This OAuth 2.0 scope grants access to YouTube Content ID API functionality.
  "https://www.googleapis.com/auth/youtubepartner",)

YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"
YOUTUBE_CONTENT_ID_API_SERVICE_NAME = "youtubePartner"
YOUTUBE_CONTENT_ID_API_VERSION = "v1"

# Authorize the request and store authorization credentials.
def get_authenticated_services():
  # Check runtime environment and use the appropriate mechanism for retrieving credentials
  if os.getenv('SERVER_SOFTWARE', '').startswith('Google App Engine/'):
    # Production
    credentials = app_engine.Credentials(scopes=YOUTUBE_SCOPES)
  else:
    # Local development server
    credentials = service_account.Credentials.from_service_account_file(
          SERVICE_ACCOUNT_FILE, scopes=YOUTUBE_SCOPES)

  youtube = googleapiclient.discovery.build("youtube", "v3", credentials=credentials)
  youtube_partner = googleapiclient.discovery.build("youtubePartner", "v1", credentials=credentials)

  return (youtube, youtube_partner)

def is_video_claimed(youtube_partner, video_id):

  # Fetch and print claims associated with video_id
  claim_search_response = youtube_partner.claimSearch().list(
    onBehalfOfContentOwner=CONTENT_OWNER_ID,
    videoId=video_id
  ).execute()

  if(claim_search_response["pageInfo"]["totalResults"]):
    claims = claim_search_response["items"]

    for c in claims:
      if c["status"] != 'inactive':
        return True

  return False

def get_video(youtube, video_id):

  video_list_response = youtube.videos().list(
    part='snippet',
    id=video_id,
    onBehalfOfContentOwner=CONTENT_OWNER_ID
  ).execute()

  if(video_list_response["pageInfo"]["totalResults"]):
    video = dict(
      id=video_list_response["items"][0]["id"],
      title=video_list_response["items"][0]["snippet"]["title"],
      description=video_list_response["items"][0]["snippet"]["description"]
    )
    return video

def get_monetize_in_all_countries_policy_id(youtube_partner):
  
  policies_list_response = youtube_partner.policies().list(
    onBehalfOfContentOwner=CONTENT_OWNER_ID
  ).execute()
  
  policies = policies_list_response["items"]

  for p in policies:
    if p["name"] == "Monetize in all countries":
      return p["id"]

  return False

def create_asset(youtube_partner, content_owner_id, title, description):
  # Create a new web asset, which corresponds to a video that was originally
  # distributed online. The asset will be linked to the corresponding YouTube
  # video via a claim that is created later in the script.
  description = description or 'None'

  body = dict(
    type="web",
    metadata=dict(
      title=title,
      description=description
    ),
    label=['pubsubhubbub-upload-claimer']
  )

  assets_insert_response = youtube_partner.assets().insert(
    onBehalfOfContentOwner=CONTENT_OWNER_ID,
    body=body
  ).execute()

  return assets_insert_response["id"]

def set_asset_ownership(youtube_partner, content_owner_id, asset_id):
  # Update the asset's ownership data. This example indicates that the content
  # owner owns 100% of the asset worldwide.
  body = dict(
    general=[dict(
      owner=CONTENT_OWNER_ID,
      ratio=100,
      type="exclude",
      territories=[]
    )]
  )

  youtube_partner.ownership().update(
    onBehalfOfContentOwner=CONTENT_OWNER_ID,
    assetId=asset_id,
    body=body
  ).execute()

def claim_video(youtube_partner, content_owner_id, asset_id, video_id,
  policy_id=None):
  # Create a claim resource. Identify the video being claimed, the asset
  # that represents the claimed content, the type of content being claimed,
  # and the policy that you want to apply to the claimed video.
  #
  # You can identify a policy by using the policy_id of an existing policy as
  # obtained via youtubePartner.policies.list(). If you update that policy at
  # a later time, the updated policy will also be applied to a claim. If you
  # do not provide a policy_id, the code creates a new inline policy that
  # indicates that the video should be monetized.
  policy_id = policy_id or POLICY_ID or get_monetize_in_all_countries_policy_id(youtube_partner)

  if policy_id:
    policy = dict(
      id=policy_id
    )
  else:
    policy = dict(
      rules=[dict(
        action="monetize"
      )]
    )

  body = dict(
    assetId=asset_id,
    videoId=video_id,
    policy=policy,
    contentType="audiovisual"
  )

  claims_insert_response = youtube_partner.claims().insert(
    onBehalfOfContentOwner=CONTENT_OWNER_ID,
    body=body
  ).execute()

  return claims_insert_response["id"]

def set_advertising_options(youtube_partner, content_owner_id, video_id):
  # Enable ads for the video. This example enables the TrueView ad format.
  body = dict(
    adFormats=[
      "overlay",
      "product_listing",
      "standard_instream",
      "trueview_instream",
      "long"
    ]
  )

  youtube_partner.videoAdvertisingOptions().update(
    videoId=video_id,
    onBehalfOfContentOwner=CONTENT_OWNER_ID,
    body=body
  ).execute()

def apply_usage_policy(video_id=None):

  if(video_id == None):
    return False

  (youtube, youtube_partner) = get_authenticated_services()

  if is_video_claimed(youtube_partner, video_id):
    logging.info('Video with ID "%s" already has active claim', video_id)
    return

  video = get_video(youtube, video_id)

  if not video:
    return False

  asset_id = create_asset(youtube_partner, CONTENT_OWNER_ID,
    video["title"], video["description"])
  logging.info("Created new asset ID '%s'." % asset_id)

  set_asset_ownership(youtube_partner, CONTENT_OWNER_ID, asset_id)
  logging.info("Successfully set asset ownership.")

  claim_id = claim_video(youtube_partner, CONTENT_OWNER_ID, asset_id,
    video_id)
  logging.info("Created new claim ID '%s'." % claim_id)

  set_advertising_options(youtube_partner, CONTENT_OWNER_ID, video_id)
  logging.info("Successfully set advertising options.")

  logging.info("All done!")

  logging.info('Video with ID "%s" claimed successfully',
                   video_id)
  return True
