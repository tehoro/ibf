#!/bin/bash
umask 000
cd /Users/miniserver/Dropbox/Developer/webforecasts/
/opt/homebrew/bin/python3 -u /Users/miniserver/Dropbox/Developer/webforecasts/make_forecasts.py > /Users/miniserver/Dropbox/Developer/webforecasts/make_forecasts.log 2> /Users/miniserver/Dropbox/Developer/webforecasts/make_forecasts_error.log
