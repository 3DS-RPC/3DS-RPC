#!/bin/bash
#
# This file will use my forks of the following repositories for functionality
# The reason for this is purely because of anynet's reliance upon netifaces
# which always fails with building on my aarch64 server. If you are using
# an architecture that may not support netifaces, please feel free to
# use this shell script.
#

if [ -d ./venv/ ]
then
  rm -rf ./venv/
fi
python3 -m venv venv
source venv/bin/activate
git clone https://github.com/MCMi460/anynet -b w/o-netifaces2
cd anynet && python -m pip install . && cd ..
rm -rf anynet
git clone https://github.com/kinnay/NintendoClients
cd NintendoClients && python -m pip install . && cd ..
rm -rf NintendoClients
python -m pip install -r requirements.txt
python -m pip install -U Flask-SQLAlchemy

# and finally, fixing up the database (i.e. restoring it to a clean slate)

if [ "$1" = 'reset' ]
then
  cd sqlite
  if [ -e ./fcLibrary.db ]
  then
    rm ./fcLibrary.db
  fi
  ./reset.sh # The user will now have to follow the on-screen directions
  cd ..
fi

# Now build with npm
if [ -d ./static/ ]
then
  rm -rf ./static/
fi
cd templates
if [ -d ./dist/ ]
then
  rm -rf ./dist/
fi
npm install .
npm run build

# Finally, end script
echo "Script ended"
