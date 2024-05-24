# Installation instructions:

Install miniconda per the website's instructions.

Run the following command and follow the instructions that follow:

	conda create -y --name OneUp python=3.11.4 pip  
	conda activate OneUp  
	bash install/install.sh

NOTE: If you are unable to run the bash script, you can enter the commands line-by-line as described below in the "Manual installation" section.  

## After installation:


Follow the steps on screen to create the admin account for the django server.

	gunicorn oneUp.wsgi -b 0.0.0.0:8000 --timeout 30 -w 4 

Type "0.0.0.0:8000/admin" into your browser URL bar and hit enter.  
NOTE: If you want to run on a different port, change 8000 to your desired port.

Log into the admin panel using the username and password you created in the previous step.

In the top-left of the page, click "Home"  
Click "Users"  
Click on your username  
Scroll down to "Groups" and click on "Choose all"  
Scroll to the bottom of the page and click "SAVE"

## Adding a new course:

Scroll down to "Instructors" and click on "Coursess"  
When the page loads, click "ADD COURSES" on the right  
Give your course a name and click "SAVE"  

In the top-left of the page, click "Instructors"  
Scroll down to "University courses" and click "Add"
Click the plus symbol next to the university dropdown menu to add your university  
Select your course from the Course dropdown menu  
Click "SAVE"

In the top-left of the page, click "Instructors"  
Scroll down to "Instructor registered courses" and click "Add"  
In the dropdown menus, select your user and your course  
Click "SAVE"

In the top-left of the page, click "Home"  
Scroll-down to "Badges", find "Course config params" and click "Add"  
Use the drop-down menu for "The related course" to select your course  
Scroll to the bottom of the page and click "SAVE"

In the top-left of the page, click "Home"  
Scroll-down to "Students", find "Students" and click "Add"  
Select your user from the "User" drop-down menu  
Enter "UniversityID" as 1  
Click "SAVE"

In the top-left of the page, click "Students"  
Scroll-down to "Student config params" and click "Add"  
Use the drop-down menu for "The related course" to select your course  
Use the drop-down menu for "The related student" to select your user  
Click "SAVE"

In the top-left of the page, click "Students"  
Scroll-down to "Student registered courses" and click "Add"  
Use the drop-down menu for "The related course" to select your course  
Use the drop-down menu for "The related student" to select your user  
Enter "AvatarImage" as 1  
Click "SAVE"


Type "0.0.0.0:8000/" into your browser URL bar and hit enter.  
The OneUp page should load and allow you to log into the account you created (you may already be logged in).  
Click "VIEW COURSES"  
Find your course and click "SELECT"  


# Manual installation

Run the following commands:

	pip install -r install/reqs.txt

	curl ifconfig.me > install/s2  
	python install/combine.py install/s1 install/s2 install/s3 install/settings.py  
	cp install/settings.py oneup/settings.py 

	python manage.py migrate  
	python manage.py collectstatic  
	python manage.py createsuperuser
