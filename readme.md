Installation instructions:

Install miniconda per the website's instructions.

Run the following commands:
conda create -y --name OneUp python=3.11.4 pip
conda activate OneUp
pip install -r install/reqs.txt

change line in pytz to add collections.abc 
NOTE: this change has been made on GitHub but not on PyPi?

curl ifconfig.me > install/s2
python install/combine.py install/s1 install/s2 install/s3 install/settings.py
cp install/settings.py oneup/settings.py

gunicorn oneUp.wsgi -b 0.0.0.0 --timeout 30 -w 4

You can then check that it is running by typing "0.0.0.0:8000" into your browser.
