# Copyright 2008 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Simple subscriber that aggregates all feeds together."""

from google.appengine.ext import vendor
vendor.add("lib/appengine-subscriber")
#vendor.add(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lib'))

import hashlib
import json
import logging
import os
import time

import feedparser
import jinja2
import webapp2
import urllib

# from appengine_subscriber import

from yt_co_ids import CHANNEL_IDS

from yt_usage_policies import apply_usage_policy

from google.appengine.ext import db
from google.appengine.api import urlfetch, app_identity

PATH_TO_APPENGINE_SUBSCRIBER_SUBMODULE = 'lib/appengine-subscriber/'

JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)


class TopicUpdate(db.Model):
  """Some topic update.

  Key name will be a hash of the feed source and item ID.
  """
  topic = db.StringProperty()
  title = db.TextProperty()
  content = db.TextProperty()
  updated = db.DateTimeProperty(auto_now_add=True)
  link = db.TextProperty()
  callback = db.StringProperty()


def parse_header_links(value):
  """Returns a list of parsed link headers.

  You can run the following examples:

  $ export PYTHONPATH=.../google3/third_party/py:${HOME}/google_appengine
  $ python -m doctest -v main.py

  >>> parse_header_links('<http://foo.com>;rel=self')
  [{'url': 'http://foo.com', 'rel': ['self']}]
  >>> parse_header_links('   <  http://foo.com  > ;     rel  = "    self  "')
  [{'url': 'http://foo.com', 'rel': ['self']}]
  >>> parse_header_links('<http://foo.com>;a;b=c;rel=self')
  [{'url': 'http://foo.com', 'a': '', 'b': 'c', 'rel': ['self']}]
  >>> parse_header_links('<http://foo.com>;rel="a b c"')
  [{'url': 'http://foo.com', 'rel': ['a', 'b', 'c']}]
  >>> parse_header_links('<http://foo.com>')
  [{'url': 'http://foo.com'}]
  >>> parse_header_links('<http://foo.com')
  [{'url': 'http://foo.com'}]
  >>> parse_header_links('rel=self')
  [{'url': 'rel=self'}]
  >>> parse_header_links('<http://foo.com>;rel=SeLf')
  [{'url': 'http://foo.com', 'rel': ['self']}]
  >>> parse_header_links('<http://foo.com>;rel=self,<http://bar.com>;rel=hub')
  [{'url': 'http://foo.com', 'rel': ['self']}, {'url': 'http://bar.com', 'rel': ['hub']}]
  """
  def cleanstr(s):
    """Removes whitespace and quotes."""
    return s.strip(' \t\"')

  res = []
  for val in value.split(','):
    try:
      url, params = val.split(';', 1)
    except ValueError:
      url, params = val, ''
    link = {}
    link['url'] = url.strip('<> ')
    for param in params.split(';'):
      try:
        key, value = param.split('=')
      except ValueError:
        key, value = param, ''
      key = cleanstr(key)
      if not key:
        continue
      if key in ('rel', 'rev'):
        link[key] = map(str.lower, cleanstr(value).split())
      else:
        link[key] = cleanstr(value)
    res.append(link)
  return res


def get_self_link(request):
  """Returns the first 'self' link found in headers."""
  for link in parse_header_links(request.headers.get('link', '')):
    if 'self' in link.get('rel', []):
      return link['url']

def get_yt_video_id(link):
  video_url = str(link)
  if("https://www.youtube.com/watch?v=" in video_url):
    return video_url.replace("https://www.youtube.com/watch?v=", "")

class InputHandler(webapp2.RequestHandler):
  """Handles feed input and subscription."""

  def get(self):
    # Just subscribe to everything.
    self.response.out.write(self.request.get('hub.challenge'))
    self.response.set_status(200)

  def post(self):
    body = self.request.body.decode('utf-8').encode(
        'ascii', 'xmlcharrefreplace')
    logging.info('Post body is %d characters', len(body))
    topic = get_self_link(self.request)

    data = feedparser.parse(body)
    if data.bozo:
      logging.error('Bozo feed data. %s: %r',
                    data.bozo_exception.__class__.__name__,
                    data.bozo_exception)
      if (hasattr(data.bozo_exception, 'getLineNumber') and
          hasattr(data.bozo_exception, 'getMessage')):
        line = data.bozo_exception.getLineNumber()
        logging.error('Line %d: %s', line, data.bozo_exception.getMessage())
        segment = body.split('\n')[line-1]
        logging.info('Body segment with error: %r', segment)
      return self.response.set_status(500)

    update_list = []
    logging.info('Found %d entries', len(data.entries))
    for entry in data.entries:
      if hasattr(entry, 'content'):
        # This is Atom.
        entry_id = entry.id
        content = entry.content[0].value
        link = entry.get('link', '')
        title = entry.get('title', '')
      else:
        content = entry.get('description', '')
        title = entry.get('title', '')
        link = entry.get('link', '')
        entry_id = (entry.get('id', '') or link or title or content)

      logging.info('Found entry in topic = "%s" with title = "%s", id = "%s", '
                   'link = "%s", content = "%s"',
                   topic, title, entry_id, link, content)
      update_list.append(TopicUpdate(
          key_name='key_' + hashlib.sha1(link + '\n' + entry_id).hexdigest(),
          topic=topic,
          title=title,
          content=content,
          link=link,
          callback=self.request.path[len('/subscriber'):]))
    db.put(update_list)

    self.response.set_status(200)
    self.response.out.write('Aight.  Saved.')

    uploads = len(update_list)
    uploads_not_claimed = 0

    for entry in update_list:
      yt_video_id = get_yt_video_id(entry.link)
      if(yt_video_id):
        try:
          apply_usage_policy(yt_video_id)
        except:
          uploads_not_claimed = uploads_not_claimed+1

    logging.info("Videos claimed: %d of %d", uploads-uploads_not_claimed, uploads)

class DebugHandler(webapp2.RequestHandler):
  """Debug handler for simulating events."""

  def get(self):
    template = JINJA_ENVIRONMENT.get_template(PATH_TO_APPENGINE_SUBSCRIBER_SUBMODULE + 'debug.html')
    self.response.out.write(template.render())


class ViewHandler(webapp2.RequestHandler):
  """Shows the items to anyone as HTML."""

  def get(self):
    template = JINJA_ENVIRONMENT.get_template(PATH_TO_APPENGINE_SUBSCRIBER_SUBMODULE + 'subscriber.html')
    self.response.write(template.render())


class CleanupHandler(webapp2.RequestHandler):
  """Keeps the last NUM_ENTRIES_TO_KEEP entries and removes the rest."""

  NUM_ENTRIES_TO_KEEP = 50000

  def get(self):
    q = db.Query(TopicUpdate, keys_only=True)
    q.order('-updated')
    for i, key in enumerate(q.run()):
      if i > self.NUM_ENTRIES_TO_KEEP:
        db.delete(key)


class ItemsHandler(webapp2.RequestHandler):
  """Gets the items."""

  def get(self):
    num_entries = self.request.get_range('num_entries', 1, 100, 25)
    callback_filter = self.request.get('callback_filter')
    query = db.Query(TopicUpdate)
    if callback_filter:
      query.filter('callback =', callback_filter)
    query.order('-updated')

    items = []
    for update in query.fetch(num_entries):
      items.append({'time_s': time.mktime(update.updated.timetuple()),
                    'topic': update.topic,
                    'title': update.title,
                    'content': update.content,
                    'source': update.link,
                    'callback': update.callback})
    self.response.out.write(json.dumps(items))

class RefreshHandler(webapp2.RequestHandler):
  """Refreshes Google Hub subscriptions to YouTube Channel topics (using CHANNEL_IDS in yt_co_ids.py)"""

  def get(self):
    # get host for callback URL - if testing locally set server_url to your YT Upload Claimer host (e.g. https://example.com)
    server_url = app_identity.get_default_version_hostname()
    count = 0

    for channel_id in CHANNEL_IDS:
      try:
        body = urllib.urlencode({
          'hub.callback': server_url+'/subscriber/'+channel_id,
          'hub.mode': 'subscribe',
          'hub.topic': 'https://www.youtube.com/feeds/videos.xml?channel_id='+channel_id,
          'hub.lease_seconds': 864000
        })
        result = urlfetch.fetch(
          url="https://pubsubhubbub.appspot.com/subscribe",
          payload=body,
          method=urlfetch.POST,
          headers={'Content-Type': 'application/x-www-form-urlencoded'})
        if result.status_code == 202:
          count=count+1
      except urlfetch.Error:
        logging.exception('Caught exception subscribing to channel ID: %s', channel_id)

      logging.info('Subscribed to %d of %d channel topics', count, len(CHANNEL_IDS))
      self.response.write(result.content)

app = webapp2.WSGIApplication(
    [(r'/items', ItemsHandler),
     (r'/debug', DebugHandler),
     # Wildcard below so we can test multiple subscribers in a single app.
     (r'/subscriber.*', InputHandler),
     (r'/', ViewHandler),
     (r'/cleanup', CleanupHandler),
     (r'/refresh', RefreshHandler)],
    debug=True)
