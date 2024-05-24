#!/bin/bash
echo "Starting installation"

pip install -r install/reqs.txt && 
curl ifconfig.me > install/s2 && 
python install/combine.py install/s1 install/s2 install/s3 install/settings.py && 
cp install/settings.py oneUp/settings.py &&
python manage.py migrate &&
python manage.py collectstatic &&
python manage.py createsuperuser

echo "Installation completed, please set up a course as dscribed in the readme."
