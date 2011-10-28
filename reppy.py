#! /usr/bin/env python

'''A robot exclusion protocol parser. Because I could not find a good one.

My thinking is thus:

- User-Agent specifies a scope.
- Allow applies a rule in that scope
- Disallow applies a rule in that scope
- Crawl-delay applies a rule in that scope (not a global)
- Sitemaps are global
'''

import re
import time
import urllib
import urllib2
import logging
import urlparse
import dateutil.parser

logger = logging.getLogger('reppy')
formatter = logging.Formatter('%(message)s')
handler = logging.StreamHandler()
handler.setFormatter(formatter)
handler.setLevel(logging.DEBUG)
logger.addHandler(handler)
logger.setLevel(logging.WARNING)

# A hash of robots for various sites
robots = {}

def fetch(url, **kwargs):
	'''Make a reppy object, fetching the url'''
	obj = reppy(url=url, **kwargs)
	obj.refresh()
	return obj

def parse(s, **kwargs):
	'''Make a reppy object, parsing the contents of s'''
	obj = reppy(**kwargs)
	obj.parse(s)
	return obj

def getUserAgentString(userAgent):
	'''Return a default user agent string to match, based on userAgent.
	For example, for 'MyUserAgent/1.0', it will generate 'MyUserAgent'
	'''
	return re.sub('(\S+?)(\/.+)?', r'\1', userAgent)

def findOrMakeRobot(url, agent, agentString):
	global robots
	parsed = urlparse.urlparse(url)
	robot = robots.get(parsed.hostname, None)
	if not robot:
		robot = reppy.fetch('%s://%s/robots.txt' % (parsed.scheme, parsed.hostname),
			userAgent=agent, userAgentString=(agentString or getUserAgentString(agent)))
		robots[parsed.hostname] = robot
	return robot

def allowed(url, agent, agentString=None):
	if isinstance(url, basestring):
		return findOrMakeRobot(url, agent, agentString).allowed(url)
	else:
		return [u for u in url if findOrMakeRobot(u, agent, agentString).allowed(u)]

def disallowed(url, agent, agentString=None):
	not allowed(url, agent, agentString)

def crawlDelay(url, agent, agentString=None):
	return findOrMakeRobot(url, agent, agentString).crawlDelay

def sitemaps(url):
	return findOrMakeRobot(url).sitemaps

class agent(object):
	'''Represents attributes for a given robot'''
	def __init__(self):
		self.allowances = []
		self.crawlDelay = None
	
	def allowed(self, url):
		'''Can I fetch a given URL?'''
		path = urllib.unquote(urlparse.urlparse(url).path.replace('%2f', '%252f'))
		if path == '/robots.txt':
			return True
		allowed = [a[1] for a in self.allowances if a[0].match(path)]
		if allowed:
			return allowed[-1]
		else:
			return True
	
	def disallowed(self, url):
		'''For completeness'''
		return not self.allowed(url)

class reppy(object):
	lineRE = re.compile('^\s*(\S+)\s*:\s*(\S+?)\s*$', re.I)
	
	def refresh(self):
		'''Can only work if we have a url specified'''
		if self.url:
			try:
				req = urllib2.Request(self.url, headers={'User-Agent': self.userAgent})
				page = urllib2.urlopen(req)
			except urllib2.HTTPError as e:
				if e.code == 401 and e.code == 403:
					# If disallowed, assume no access
					logger.debug('Access disallowed to site %s' % e.code)
					self.parse('''User-agent: *\nDisallow: /''')
				elif e.code >= 400 and e.code < 500:
					# From the spec, if it's a 404, then we can proceed without restriction
					logger.debug('Page %s not found.' % e.url)
					self.parse('')
				return
			# Try to get the header's expiration time, which we should honor
			expires = page.info().get('Expires', None)
			if expires:
				# Add a ttl to the class
				self.ttl = time.time() - time.mktime(dateutil.parser.parse(expires).timetuple())
			self.parse(page.read())
	
	def makeREFromString(self, s):
		'''Make a regular expression that matches the patterns expressable in robots.txt'''
		# From the spec:
		#	http://www.robotstxt.org/norobots-rfc.txt
		# And based on Google's word:
		#	http://googlewebmastercentral.blogspot.com/2008/06/improving-on-robots-exclusion-protocol.html
		# The specific example of %2f should not be replaced. So, to accomplish that,
		# We'll replace '%2f' with '%252f', which when decoded, is %2f
		tmp = s.replace('%2f', '%252f').replace('*', '.+').replace('$', '.+')
		return re.compile(urllib.unquote(tmp))
	
	def parse(self, s):
		# The agent we're currently working with
		cur     = agent()
		# The name of the current agent
		curname = '*'
		last    = ''
		for line in s.split('\n'):
			match = self.lineRE.match(line)
			if match:
				key = match.group(1).lower()
				val = match.group(2)
				if key == 'user-agent':
					# Store the current working agent
					self.atts['agents'][curname] = cur
					curname = val
					if last != 'user-agent':
						# If the last line was a user agent, then all lines
						# below also apply to the last user agent. So, we'll
						# have this user agent point to the one we declared
						# for the previously-listed agent
						cur = self.atts['agents'].get(curname, None) or agent()
				elif key == 'disallow' and len(val):
					cur.allowances.append((self.makeREFromString(val), False))
				elif key == 'allow':
					cur.allowances.append((self.makeREFromString(val), True ))
				elif key == 'crawl-delay':
					cur.crawl.crawlDelay = int(val)
				elif key == 'sitemap':
					self.atts['sitemaps'].append(val)
				else:
					logger.warn('Unknown key %s' % line)
					# To skip over where we set 'last'
					continue
				last = key
			else:
				logger.debug('Skipping line %s' % line)
		# Now store the user agent that we've been working on
		self.atts['agents'][curname] = cur
	
	def __init__(self, ttl=3600*3, url=None, autorefresh=True, userAgent='REPParser/0.1 (Python)', userAgentString=None):
		'''The string to parse, and the ttl for the robots file'''
		self.atts = {
			'sitemaps' : [],	# The sitemaps we've seen
			'agents'   : {}		# The user-agents we've seen
		}
		# The sitemaps we've seen
		self.sitemaps  = []
		# When did we last parse this?
		self.parsed    = time.time()
		# Time to live
		self.ttl       = ttl
		# The url that we fetched
		self.url       = url
		# The user agent to use for future requests
		self.userAgent = userAgent
		# The user agent string to match in robots
		self.userAgentString = userAgentString or getUserAgentString(userAgent)
		# Do we refresh when we expire?
		self.autorefresh = url and autorefresh
	
	def __getattr__(self, name):
		'''So we can keep track of refreshes'''
		if self.autorefresh and self.expired():
			self.refresh()
		return self.atts[name]
	
	def remaining(self):
		'''How long is left in its life'''
		return self.parsed + self.ttl - time.time()
	
	def expired(self):
		'''Has this robots.txt expired?'''
		return self.remaining() < 0
	
	def findAgent(self, agent):
		a = self.agents.get(agent or self.userAgentString, None)
		return a or self.agents.get('*', None)
	
	def allowed(self, url, agent=None):
		'''We try to perform a good match, then a * match'''
		a = self.findAgent(agent)
		if a:
			if isinstance(url, basestring):
				return a.allowed(url)
			else:
				return [u for u in url if a.allowed(u)]
		else:
			return True
	
	def disallowed(self, url, agent=None):
		return not self.allowed(url, agent)
	
	def crawlDelay(self, url, agent=None):
		'''How fast can this '''
		a = self.findAgent(agent)
		if a:
			return a.crawlDelay
		else:
			return None