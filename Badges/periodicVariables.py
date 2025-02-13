import json
import logging
import random
from _datetime import date, datetime
from contextlib import contextmanager
from datetime import timedelta

import _cffi_backend
from billiard.connection import CHALLENGE
from celery.bin.result import result
from dateutil.utils import today
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django_celery_beat.models import (CrontabSchedule, PeriodicTask,
                                       PeriodicTasks)

from Badges.celeryApp import app
from Instructors.views.utils import (current_localtime, current_utctime,
                                     datetime_to_local)

# Get an instance of a logger
logger = logging.getLogger(__name__)


LOCK_EXPIRE = 60 * 5 # lock expire in 5 minutes

@contextmanager
def memcache_lock(lock_id, oid):
    timeout_at = time.monotonic() + LOCK_EXPIRE - 3
    # cache.add fails if the key already exists
    status = cache.add(lock_id, oid, LOCK_EXPIRE)
    try:
        yield status
    finally:
        # memcache delete is very slow, but we have to use it to take
        # advantage of using add() for atomic locking
        if time.monotonic() < timeout_at and status:
            # don't release the lock if we exceeded the timeout
            # to lessen the chance of releasing an expired lock
            # owned by someone else
            # also don't release the lock if we didn't acquire it
            cache.delete(lock_id)

def setup_periodic_badge(unique_id, badge_id, variable_index, course, period_index, number_of_top_students=None, threshold=1, operator_type='>', is_random=None, request=None):
    ''' unique_id should be the created id for periodic badge object. badge_id is the id of the badge to award students'''
    unique_str = str(unique_id)+"_badge"
    return setup_periodic_variable(unique_id, unique_str, variable_index, course, period_index, number_of_top_students=number_of_top_students, threshold=threshold, operator_type=operator_type, is_random=is_random, badge_id=badge_id, request=request)

def setup_periodic_vc(unique_id, virtual_currency_amount, variable_index, course, period_index, number_of_top_students=None, threshold=1, operator_type='>', is_random=None, request=None):
    ''' unique_id should be the created id for periodic vc object. virtual_currency_amount is the amount to award students.'''
    unique_str = str(unique_id)+"_vc"
    return setup_periodic_variable(unique_id, unique_str, variable_index, course, period_index, number_of_top_students=number_of_top_students, threshold=threshold, operator_type=operator_type, is_random=is_random, virtual_currency_amount=virtual_currency_amount, request=request)
    
def setup_periodic_leaderboard(leaderboard_id, variable_index, course, period_index, number_of_top_students=None, threshold=1, operator_type='>', is_random=None, request=None):
    ''' leaderboard_id should be the created if for periodic learderboard object'''
    unique_str = str(leaderboard_id)+"_leaderboard"
    return setup_periodic_variable(leaderboard_id, unique_str, variable_index, course, period_index, number_of_top_students=number_of_top_students, threshold=threshold, operator_type=operator_type, is_random=is_random, is_leaderboard=True, save_results=True, request=request)

def setup_periodic_variable(unique_id, unique_str, variable_index, course, period_index, number_of_top_students=None, threshold=1, operator_type='>', is_random=None,  is_leaderboard=False, badge_id=None, virtual_currency_amount=None, save_results=False, request=None):
    ''' Creates Periodic Task if not created with the provided periodic variable function and schedule.'''
    periodic_variable = PeriodicVariables.periodicVariables[variable_index]
    if request:
        timezone = request.session['django_timezone']
    else:
        timezone = settings.TIME_ZONE

    periodic_task, _ = PeriodicTask.objects.get_or_create(
        name=periodic_variable['name']+'_'+unique_str,
        kwargs=json.dumps({
            'unique_id': unique_id,
            'timezone': timezone,
            'variable_index': variable_index,
            'course_id': course.courseID,
            'period_index': period_index,
            'number_of_top_students': number_of_top_students,
            'threshold': threshold,
            'operator_type': operator_type,
            'is_random': is_random,
            'is_leaderboard': is_leaderboard,
            'badge_id': badge_id,
            'virtual_currency_amount': virtual_currency_amount,
            'save_results': save_results
        }),
        task='Badges.periodicVariables.periodic_task',
        crontab=TimePeriods.timePeriods[period_index]['schedule'](timezone),
        one_off=period_index == TimePeriods.beginning_of_time
    )
    return periodic_task

def get_periodic_variable_results_for_student(variable_index, period_index, course_id, student, timezone):
    ''' This function will get any periodic variable results without the use of celery.
        The time period is used for how many days/minutes to go back from now.
        Ex. Time Period: Weekly - Return results within 7 days ago
        
        Returns list of tuples: [(student, value), (student, value),...]
    '''
    from Students.models import StudentRegisteredCourses
    periodic_variable = PeriodicVariables.periodicVariables[variable_index]
    time_period = TimePeriods.timePeriods[period_index]
    # Get the course object and periodic variable
    course = get_course(course_id)
    packet = {
        'unique_id': None,
        'student': student,
        'course': course,
        'periodic_variable': periodic_variable,
        'time_period': time_period,
        'award_type': None,
        'timezone': timezone,
        'last_ran': time_period['datetime'](timezone)
    }
    # Evaluate student based on periodic variable function
    return periodic_variable['function'](packet, result_only=True)

def get_periodic_variable_results(variable_index, period_index, course_id, timezone):
    ''' This function will get any periodic variable results without the use of celery.
        The time period is used for how many days/minutes to go back from now.
        Ex. Time Period: Weekly - Return results within 7 days ago
        
        Returns list of tuples: [(student, value), (student, value),...]
    '''
    from Students.models import StudentRegisteredCourses
    periodic_variable = PeriodicVariables.periodicVariables[variable_index]
    time_period = TimePeriods.timePeriods[period_index]
    # Get the course object and periodic variable
    course = get_course(course_id)
    # Get all the students in this course except test students
    students = StudentRegisteredCourses.objects.filter(courseID=course, studentID__isTestStudent=False)
    rank = []
    # Evaluate each student based on periodic variable function
    for student_in_course in students:
        rank.append(get_periodic_variable_results_for_student(variable_index, period_index, course_id, student_in_course.studentID, timezone))
    return rank

def delete_periodic_task(unique_id, variable_index, award_type, course):
    ''' Deletes Periodic Task when rule or badge is deleted'''

    if award_type != "badge" and award_type != "vc" and award_type != "leaderboard":
        logger.error("Cannot delete Periodic Task Object: award_type is not 'badge' or 'vc'!!")
        return None

    periodic_variable = PeriodicVariables.periodicVariables[variable_index]
    unique_str = str(unique_id)+"_"+award_type
    PeriodicTask.objects.filter(name=periodic_variable['name']+'_'+unique_str, kwargs__contains='"course_id": '+str(course.courseID)).delete()

def get_course(course_id):
    ''' Method to get the course object from course id'''
    from Instructors.models import Courses
    course = Courses.objects.get(pk=int(course_id))
    return course


@app.task(ignore_result=True)
def periodic_task(unique_id, timezone, variable_index, course_id, period_index, number_of_top_students, threshold, operator_type, is_random, is_leaderboard=False, badge_id=None, virtual_currency_amount=None, save_results=False): 
    ''' Celery task which runs based on the time period (weekly, daily, etc). This task either does one of the following
        with the results given by the periodic variable function:
            1. Takes the top number of students specified by number_of_top_students variable above a threshold
            2. Takes the students above a threshold based on students results
            3. Takes a student at random above a threshold based on students results
            4. Take all the students above a threshold
        Then awards the student(s) with a badge or virtual currency.

        If badge_id is provided the student(s) will be given a badge.
        If virtual_currency_amount is provied the student(s) will be given virtual currency.
        Threshold can be either: max, avg, or some string number

        Note: unique_id is either PeriodicBadgeID(badgeID) or VirtualCurrencyPeriodicRuleID(vcRuleID) or leaderboard id
        Note: operator_type is string, is_random is a boolean, and threshold is a string. Everything else should be an integer
        None: if number_of_top_students and is_random is null then all is assumed (see number 4)
    '''
    from Students.models import StudentRegisteredCourses, PeriodicallyUpdatedleaderboards
    from Badges.models import CourseConfigParams

    # Get the periodic variable and time period
    periodic_variable = PeriodicVariables.periodicVariables[variable_index]
    time_period = TimePeriods.timePeriods[period_index]

    course = get_course(course_id)
    
    # Set the award type used for finding Periodic Task object
    award_type = ""
    if badge_id is not None:
        award_type += "badge"
    if virtual_currency_amount is not None:
        award_type += "vc"
    if is_leaderboard:
        award_type += "leaderboard"

    unique_str = str(unique_id)+"_"+award_type
    task_id = periodic_variable['name']+'_'+unique_str
    print("RUNNING PV - {} - {} - {} - {}".format(task_id, time_period['name'], timezone, course.courseName))
    
    periodic_task = PeriodicTask.objects.get(name=task_id, kwargs__contains='"course_id": '+str(course_id))

    # Get the course
    today = current_utctime().date()
    course_config = CourseConfigParams.objects.get(courseID=course)
    if course_config.courseStartDate > today or course_config.courseEndDate < today:
        print("Today: {} Course End Date: {} Course Start Date: {}".format(today,course_config.courseEndDate,course_config.courseStartDate ))
        periodic_task.delete()
        print("DELETE OLD PV - {} - {} - {} - {}".format(task_id, time_period['name'], timezone, course.courseName))
        return

    # Aquire the lock for the task 
    lock_id = "lock-{}".format(task_id)
    with memcache_lock(lock_id, app.oid) as acquired:
        if not acquired:
            print("NO LOCK PV - {} - {} - {} - {}".format(task_id, time_period['name'], timezone, course.courseName))
            return
    
    last_ran = get_last_ran(unique_id, periodic_variable['index'], award_type, course.courseID)
    if time_period == TimePeriods.timePeriods[TimePeriods.beginning_of_time]:
        # If it has ran once then return and set it not to run anymore
        if last_ran:
            periodic_task.enabled = False
            periodic_task.save()
            PeriodicTasks.changed(periodic_task)
            print("END COMPLETE PV - {} - {} - {} - {}".format(task_id, time_period['name'], timezone, course.courseName))
            return
    
    
    time_range = time_period['datetime'](timezone)
    
    # Double check if the time period is of beginning_of_time. There should be no log for this type
    if not time_range is None and last_ran:
        # If not, call periodic_task with parameters (entry log will get updated by this call, hopefully)
        if last_ran.replace(microsecond=0, second=0) > time_range.replace(microsecond=0, second=0):
            print(f"Now: {current_localtime(tz=timezone)} UTC: {current_utctime()}")
            print(f"Last: {last_ran.replace(microsecond=0, second=0)}")
            print(f"Max for Last: {time_range.replace(microsecond=0, second=0)}")
            print("SKIP PV - {} - {} - {} - {}".format(task_id, time_period['name'], timezone, course.courseName))
            return

    # Get all the students in this course, exclude test student
    students = StudentRegisteredCourses.objects.filter(courseID=course, studentID__isTestStudent=False)
    rank = []
    
    packet = {
        'unique_id': unique_id,
        'course': course,
        'periodic_variable': periodic_variable,
        'time_period': time_period,
        'award_type': award_type,
        'timezone': timezone,
        'last_ran': last_ran
    }
    # Evaluate each student based on periodic variable function
    for student_in_course in students:
        packet.update({'student': student_in_course.studentID})
        rank.append(periodic_variable['function'](packet, result_only=False))

    print("Results: {}".format(rank))
    # Filter out students based on periodic badge/vc rule settings
    if not is_leaderboard and not save_results:
        rank = filter_students(rank, number_of_top_students, threshold, operator_type, is_random)
        print("Final Filtered ({}, {}, {}, {}): {}".format(number_of_top_students, threshold, operator_type, is_random, rank))
        # Give award to students
        award_students(rank, course, unique_id, timezone, badge_id, virtual_currency_amount)
    elif save_results:
        if is_leaderboard:
            # Get top n students and save to leaderboard
            rank = filter_students(rank, number_of_top_students+1, 0, '>=', False)
            print("Final Filtered Leaderboard ({}, {}, {}, {}): {}".format(number_of_top_students+1, 0, '>=', False, rank))
            savePeriodicLeaderboardResults(rank, unique_id, course)
              
    # periodic_task.total_run_count += 1
    # periodic_task.save()
    # PeriodicTasks.changed(periodic_task)

    # Create/update celery log so it can be used for a fallback option if this task didn't run on time
    update_celery_log_entry(task_id, {
        'unique_id': unique_id,
        'timezone': timezone,
        'variable_index': variable_index,
        'course_id': course_id,
        'period_index': period_index,
        'number_of_top_students': number_of_top_students,
        'threshold': threshold,
        'operator_type': operator_type,
        'is_random': is_random,
        'is_leaderboard': is_leaderboard,
        'badge_id': badge_id,
        'virtual_currency_amount': virtual_currency_amount,
        'save_results': save_results
    })

    print("END COMPLETE PV - {} - {} - {} - {}".format(task_id, time_period['name'], timezone, course.courseName))

def update_celery_log_entry(task_id, parameters):
    ''' Create/update celery log so it can be used for a fallback option if this task didn't run on time '''
    from Badges.models import CeleryTaskLog
    
    celery_log_entry = CeleryTaskLog.objects.filter(taskID=task_id).first()
    if celery_log_entry is None:
        # Create new celery log entry
        celery_log_entry = CeleryTaskLog()
        celery_log_entry.taskID = task_id

    celery_log_entry.parameters = json.dumps(parameters)
    celery_log_entry.timestamp = current_localtime(tz=parameters['timezone'])
    celery_log_entry.save()

def get_last_ran(unique_id, variable_index, award_type, course_id):
    ''' Retrieves the last time a periodic task has ran. 
        Returns None if it is has not ran yet.
    '''
    from Badges.models import CeleryTaskLog
    # print("award type", award_type)
    if not "badge" in award_type  and not "vc" in award_type and not "leaderboard" in award_type:
        logger.error("Cannot find Periodic Task Object: award_type is not 'badge' or 'vc' or 'leaderboard'!!")
        return None

    periodic_variable = PeriodicVariables.periodicVariables[variable_index]
    unique_str = str(unique_id)+"_"+award_type
    task_id = periodic_variable['name']+'_'+unique_str
    celery_log_entry = CeleryTaskLog.objects.filter(taskID=task_id).first()

    if celery_log_entry is None:
        return None

    return celery_log_entry.timestamp

def filter_students(students, number_of_top_students, threshold, operator_type, is_random):
    ''' Filters out students based on parameters if they are not None.
        number_of_top_students: gets the top number of students wanted
        threshold & operator_type: gets the students which are above/at a threshold
                                    threshold can be a value or a string (max, avg, etc)
        is_random: randomly chooses a student. Can be paired with threshold.
    '''

    if students:
        operatorType = {
            '>=': lambda x, y : x >= y,
            '>' : lambda x, y : x > y,
            '=' : lambda x, y : x == y
        }
        # Filter students based on if their result passes the threshold
        if not threshold in ['avg', 'max']:
            students = [(student, val) for student, val in students if operatorType[operator_type](val, int(threshold))]
        else:
            if threshold == 'max':
                max_val = max(students, key=lambda item:item[1])[1]
                # If the max value is 0 or less then we should not award any students
                if max_val <= 0:
                    students = []
                    return students

                students = [(student, val) for student, val in students if operatorType[operator_type](val, max_val)]
            elif threshold == 'avg':
                avg = round(sum([val for _, val in students])/len(students), 1)
                students = [(student, val) for student, val in students if operatorType[operator_type](val, avg)]

        if number_of_top_students and students:
            # Sort the students
            students.sort(key=lambda tup: tup[1], reverse=True)
            # Check if what we want is greater than the number of students
            if len(students) >= number_of_top_students:
                # Get the top n (number_of_top_students) unique values to handle ties
                top_values = sorted(set([val for student, val in students]), reverse=True)
                top_values = top_values[:number_of_top_students]
                # Only select the students if their value is in the top_values
                students = [(student, val) for student, val in students if val in top_values]
        elif is_random and students:
            # If random, choose one student and remove everyone else
            random.shuffle(students)
            students = random.sample(students, 1)
    return students

def award_students(students, course, unique_id, timezone, badge_id=None, virtual_currency_amount=None):
    ''' Awards students a badge or virtual currency or both.'''

    from notify.signals import notify  
    from Students.models import StudentBadges, StudentRegisteredCourses, StudentVirtualCurrency
    from Badges.models import BadgesInfo, VirtualCurrencyPeriodicRule, BadgesVCLog
    from Instructors.views.whoAddedVCAndBadgeView import create_badge_vc_log_json
    from Badges.enums import Event
    from Badges.events import register_event_simple

    for student, result in students:
        # Give award for either badge or virtual currency
        if badge_id:
            # Check if student has earned this badge
            studentBadges = StudentBadges.objects.filter(studentID = student, badgeID = badge_id)

            # If the badge has not already been earned, then award it 
            badge = BadgesInfo.objects.get(pk=badge_id)
   
            studentBadge = StudentBadges()
            studentBadge.studentID = student
            studentBadge.badgeID = badge
            studentBadge.objectID = 0
            studentBadge.timestamp = current_localtime(tz=timezone)
            studentBadge.save()

            # Record this trasaction in the log to show that the system awarded this badge
            studentAddBadgeLog = BadgesVCLog()
            studentAddBadgeLog.courseID = course
            log_data = create_badge_vc_log_json("System", studentBadge, "Badge", "Time-Period")
            studentAddBadgeLog.log_data = json.dumps(log_data)
            studentAddBadgeLog.timestamp = current_localtime(tz=timezone)
            studentAddBadgeLog.save()

            mini_req = {
                'currentCourseID': course.pk,
                'user': student.user.username,
                'timezone': timezone,
            }
            register_event_simple(Event.badgeEarned, mini_req, student, badge_id)
            # Notify student of badge award 
            notify.send(None, recipient=student.user, actor=student.user, verb='You won the '+badge.badgeName+' badge', nf_type='Badge', extra=json.dumps({"course": str(course.courseID), "name": str(course.courseName), "related_link": '/oneUp/students/StudentCourseHome'}))
            
        # Give award of virtual currency
        if virtual_currency_amount:
            if virtual_currency_amount > 0:
                student_profile = StudentRegisteredCourses.objects.get(courseID=course, studentID=student)
                student_profile.virtualCurrencyAmount += virtual_currency_amount
                student_profile.save()
                
                periodicVC = VirtualCurrencyPeriodicRule.objects.get(vcRuleID=unique_id, courseID=course)

                transaction = StudentVirtualCurrency()
                transaction.courseID = course
                transaction.studentID = student
                transaction.objectID = 0
                transaction.vcName = periodicVC.vcRuleName
                transaction.vcDescription = periodicVC.vcRuleDescription
                transaction.value = virtual_currency_amount
                transaction.timestamp = current_localtime(tz=timezone)
                transaction.save()

                # Record this trasaction in the log to show that the system awarded this badge
                studentAddBadgeLog = BadgesVCLog()
                studentAddBadgeLog.courseID = course
                log_data = create_badge_vc_log_json("System", transaction, "VC", "Time-Period")
                studentAddBadgeLog.log_data = json.dumps(log_data)
                studentAddBadgeLog.timestamp = current_localtime(tz=timezone)
                studentAddBadgeLog.save()
                
                mini_req = {
                    'currentCourseID': course.pk,
                    'user': student.user.username,
                    'timezone': timezone,
                }
                register_event_simple(Event.virtualCurrencyEarned, mini_req, student, virtual_currency_amount)
                # Notify student of VC award 
                notify.send(None, recipient=student.user, actor=student.user, verb='You won '+str(virtual_currency_amount)+' course bucks', nf_type='Increase VirtualCurrency', extra=json.dumps({"course": str(course.courseID), "name": str(course.courseName), "related_link": '/oneUp/students/Transactions'}))

def savePeriodicLeaderboardResults(rank,leaderboardConfigID,course):
    #"saving results", rank, leaderboardConfigID, course)
    from Students.models import PeriodicallyUpdatedleaderboards
    from Badges.models import LeaderboardsConfig
  
    leaderboardConfigID = LeaderboardsConfig.objects.get(leaderboardID=int(leaderboardConfigID))
    #"leaderboardConfigID", leaderboardConfigID)
    #"rank", rank)
   
    #we must filter out the test student
    studentsPlusScores = []
    for student in rank:
        if not student[0].isTestStudent:
            studentsPlusScores.append(student)
    index = 1
    
    studentsWithoutZeroes = []
    #filter out the zero point scores if any
    for student in studentsPlusScores:
        if student[1] != 0 or student[1] != 0.0:
            studentsWithoutZeroes.append(student)
    
    studentsPlusScores = studentsWithoutZeroes
    #iterate over the list of studentsplusscores and make the records or update existing records
    for student in studentsPlusScores:
        #"currentStudent", student,"index" ,index)
        leaderboardRecord = PeriodicallyUpdatedleaderboards.objects.filter(leaderboardID=int(leaderboardConfigID.leaderboardID), studentID=student[0])
        #"leaderboard", leaderboardRecord)
        
        if leaderboardRecord:
            #we have a record so we should update", leaderboardRecord[0])
            leaderboard = leaderboardRecord[0]
            leaderboard.studentID = student[0]
            leaderboard.studentPoints = student[1]
            leaderboard.studentPosition = index
        else:
            #"creating a new one since we dont have a record for"
            leaderboard = PeriodicallyUpdatedleaderboards()
            leaderboard.leaderboardID = leaderboardConfigID
            leaderboard.studentID = student[0]
            leaderboard.studentPoints = student[1]
            leaderboard.studentPosition = index
        leaderboard.save()
        index = index + 1
    #remaining records should be set to zero   
    leaderboardRecords = PeriodicallyUpdatedleaderboards.objects.filter(leaderboardID=int(leaderboardConfigID.leaderboardID), studentID=student[0])  
    if index <= len(leaderboardRecords):
        for leaderboard in leaderboardRecords[index:]:
            #"setting -1 to records after our index, no student can be -1")
            leaderboard.studentPosition = -1
            leaderboard.save()

def calculate_student_earnings(info, result_only=False):
    ''' This calculates the student earnings of virtual currency since the last period.
        Earnings are defined by only what virtual currency they gained and not spent.'''
    
    print("Calculating Highest Earner") 
    from Students.models import StudentVirtualCurrency
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule, LeaderboardsConfig
    
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']
    
    # Get the earnings for this student
    earnings = StudentVirtualCurrency.objects.filter(studentID = student, courseID = course)
    # If this is not the first time running, only get the earnings since last ran
    if last_ran:
        earnings = earnings.filter(timestamp__gte=last_ran)
    else:
        # Set the last ran to equal to the time the rule/badge was created/modified since we don't want to get all the previous earnings from beginning of time
        if award_type == 'badge':
            periodic_badge = PeriodicBadges.objects.get(badgeID=unique_id, courseID=course)
            earnings = earnings.filter(timestamp__gte=periodic_badge.lastModified)
        elif award_type == 'vc':
            periodicVC = VirtualCurrencyPeriodicRule.objects.get(vcRuleID=unique_id, courseID=course)
            earnings = earnings.filter(timestamp__gte=periodicVC.lastModified)
        elif award_type == 'leaderboard':
            periodic_leaderboard =  LeaderboardsConfig.objects.get(leaderboardID=unique_id, courseID=course)
            earnings = earnings.filter(timestamp__gte=periodic_leaderboard.lastModified)

    from Badges.events import earning_transaction_total
    
    # Get the total earnings only if they have earned more than 0
    total = earning_transaction_total(earnings)
   
    print("Course: {}".format(course))
    print("Student: {}".format(student))
    print("Periodic Variable: {}".format(periodic_variable))
    print("Last Ran: {}".format(last_ran))
    print("Total Earnings: {}".format(total))

    return (student, total)

def calculate_student_warmup_practice(info, result_only=False):
    ''' This calculates the number of times the student has practiced Warm-up challenges. This includes the
        number of times the student has completed the same challenge.
        Ex. Warm-up Challenge A: practiced 10 times
            Warm-up Challenge B: practice 2 times
            Total practiced: 12 times
    '''
    
    print("Calculating Student Practice") 
    from Instructors.models import Challenges
    from Students.models import StudentChallenges
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule, LeaderboardsConfig
    
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']

    # Check aganist only Warm-up challenges
    challenges = Challenges.objects.filter(courseID=course, isGraded=False)
    # The amount of times the student has practice Warm-up challenges
    practices = 0

    # If theere are no Warm-up challenges then we just say the student didn't practice anything :)
    if not challenges.exists():
        return (student, practices)
    
    # If this is first time running, set the last ran to equal to the time the rule/badge was created/modified since we don't want to get all the previous challenges from beginning of time
    if not last_ran:
        if award_type == 'badge':
            periodic_badge = PeriodicBadges.objects.get(badgeID=unique_id, courseID=course)
            last_ran = periodic_badge.lastModified
        elif award_type == 'vc':
            periodicVC = VirtualCurrencyPeriodicRule.objects.get(vcRuleID=unique_id, courseID=course)
            last_ran = periodicVC.lastModified
        elif award_type == 'leaderboard':
            periodic_leaderboard =  LeaderboardsConfig.objects.get(leaderboardID=unique_id, courseID=course)
            last_ran = periodic_leaderboard.lastModified

    for challenge in challenges:
        # Get the student completed Warm-up challenges (Endtimestamp is not null if it is complete)
        studentChallenges = StudentChallenges.objects.filter(courseID=course, studentID=student, challengeID=challenge.challengeID).exclude(endTimestamp__isnull=True)
        # Only get the student challenges since last ran
        if last_ran:
            studentChallenges = studentChallenges.filter(endTimestamp__gte=last_ran)           

        if studentChallenges.exists():
            print("Student Challenges: {}".format(studentChallenges))
            practices += len(studentChallenges)

    print("Course: {}".format(course))
    print("Student: {}".format(student))
    print("Periodic Variable: {}".format(periodic_variable))
    print("Last Ran: {}".format(last_ran))
    print("Practices: {}".format(practices))

    return (student, practices)


def daterange(start_date, end_date):
    ''' Returns date between two dates. both parameters are inclusive '''
    for n in range(int ((end_date - start_date).days + 1)):
        yield start_date + timedelta(n)

def calculate_number_of_days_of_unique_warmups(info, percentage, warmups_completed_in_a_day=1, result_only=False):
    ''' Utility function for getting the number of days of unique warmup challenges that a student completed with a score > percentage'''

    print("Calculating number of days of unique warmups completed with score > {}%".format(percentage))
    from Instructors.models import Challenges
    from Students.models import StudentChallenges
    from decimal import Decimal
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule
    
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']

    # Check against only Warm-up challenges
    challenges = Challenges.objects.filter(courseID=course, isGraded=False)
    
    # The amount of days
    days = 0

    # If there are no Warm-up challenges then we just say the student didn't complete anything
    if not challenges.exists():
        return (student, days)
    
    # If this is first time running, set the last ran to equal to the time the rule/badge was created/modified since we don't want to get all the previous challenges from beginning of time
    if not last_ran:
        if award_type == 'badge':
            periodic_badge = PeriodicBadges.objects.get(badgeID=unique_id, courseID=course)
            last_ran = periodic_badge.lastModified
        elif award_type == 'vc':
            periodicVC = VirtualCurrencyPeriodicRule.objects.get(vcRuleID=unique_id, courseID=course)
            last_ran = periodicVC.lastModified
        elif award_type == 'leaderboard':
            periodic_leaderboard =  LeaderboardsConfig.objects.get(leaderboardID=unique_id, courseID=course)
            last_ran = periodic_leaderboard.lastModified

    studentChallenges = StudentChallenges.objects.filter(courseID=course, studentID=student).exclude(endTimestamp__isnull=True).order_by('endTimestamp')
    # print(timezone.localtime(studentChallenges.last().endTimestamp))
    if last_ran:
        # print("LAST_RAN: {}".format(timezone.localtime(last_ran)))
        studentChallenges = studentChallenges.filter(endTimestamp__gt=last_ran)

    # If there are no Warm-up challenges taken then we just cant do anything
    if not studentChallenges.exists():
        return (student, days)

    start_date = studentChallenges.first().endTimestamp.date()
    end_date = current_localtime(tz=current_timezone).date()
    for current_day in daterange(start_date, end_date):
        challenges_for_day = studentChallenges.filter(endTimestamp__date=current_day)
        
        if challenges_for_day.exists():
            total_completed = 0
            for challenge in challenges:
                warmup_challenges = challenges_for_day.filter(challengeID=challenge.challengeID)

                if warmup_challenges.exists():
                    # Get the total possible score (Note: total score is Decimal type) for this challenge
                    total_score_possible = challenge.getCombinedScore()
                    # Get the highest score out of the student attempts for this challenge (Note: test score is Decimal type)
                    highest_score = max([warmup.getScore() for warmup in warmup_challenges])
                    print("Highest score: {}".format(highest_score))
                    print("possible score {}".format(total_score_possible))

                    # If the total possible score is not set then skip this warmup challenge
                    if total_score_possible <= 0:
                        continue
                    
                    # Calculate the percentage
                    student_percentage = (highest_score/total_score_possible) * Decimal(100)
                    print("student percentage {}".format(student_percentage))
                    print("percentage {}".format(Decimal(percentage)))
                    # Say this challenge is counted for if the student score percentage is greater than 80%
                    if student_percentage >= Decimal(percentage):
                        total_completed += 1
            
            # If the challenges taken for this day meets the threshold then count this day
            if total_completed >= warmups_completed_in_a_day:
                days += 1

    print("Course: {}".format(course))
    print("Student: {}".format(student))
    print("Periodic Variable: {}".format(periodic_variable))
    print("Last Ran: {}".format(last_ran))
    print("Days Unique Warm-ups: {}".format(days))
    return (student, days)

def calculate_number_of_days_of_unique_warmups_greater_than_90(info, result_only=False):
    ''' This will return the number of days of unique warmup challenges that a student completed with a 
        score >= 90%'''

    return calculate_number_of_days_of_unique_warmups(info, 90.0, 1, result_only)

def calculate_number_of_days_of_unique_warmups_greater_than_70(info, result_only=False):
    ''' This will return the number of days of unique warmup challenges that a student completed with a 
        score >= 70%'''

    return calculate_number_of_days_of_unique_warmups(info, 70.0, 1, result_only)

def calculate_number_of_days_of_2_unique_warmups_greater_than_80(info, result_only=False):
    ''' This will return the number of days of unique warmup challenges that a student completed with a 
        score >= 80%'''

    return calculate_number_of_days_of_unique_warmups(info, 80.0, 2, result_only)
def calculate_callouts_sent(info, result_only=False):
    from Students.models import Callouts
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule, LeaderboardsConfig
    
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
    info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']
    
    callouts = Callouts.objects.filter(courseID=course, sender=student)
    # If this is not the first time running, only get the callouts since last ran
    if last_ran:
        callouts = callouts.filter(sendTime__gte=last_ran)
    else:
        # Set the last ran to equal to the time the rule/badge was created/modified since we don't want to get all the previous callouts from beginning of time
        if award_type == 'badge':
            periodic_badge = PeriodicBadges.objects.get(badgeID=unique_id, courseID=course)
            callouts = callouts.filter(sendTime__gte=periodic_badge.lastModified)
        elif award_type == 'vc':
            periodicVC = VirtualCurrencyPeriodicRule.objects.get(vcRuleID=unique_id, courseID=course)
            callouts = callouts.filter(sendTime__gte=periodicVC.lastModified)
        elif award_type == 'leaderboard':
            periodic_leaderboard =  LeaderboardsConfig.objects.get(leaderboardID=unique_id, courseID=course)
            callouts = callouts.filter(sendTime__gte=periodic_leaderboard.lastModified)



        
    # Get the total callouts only if they have earned more than 0
    total = len(callouts)
   
    print("Course: {}".format(course))
    print("Student: {}".format(student))
    print("Periodic Variable: {}".format(periodic_variable))
    print("Last Ran: {}".format(last_ran))
    print("Total callouts: {}".format(total))

    return (student, total)

def calculate_duels_sent(info, result_only=False, total_only=False):
    from Students.models import DuelChallenges
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule, LeaderboardsConfig
    
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
    info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']
    
    duelChallenges = DuelChallenges.objects.filter(courseID=course, challenger=student)
    # If this is not the first time running, only get the duelChallenges since last ran
    if last_ran:
        duelChallenges = duelChallenges.filter(sendTime__gte=last_ran)
    else:
        # Set the last ran to equal to the time the rule/badge was created/modified since we don't want to get all the previous duelChallenges from beginning of time
        if award_type == 'badge':
            periodic_badge = PeriodicBadges.objects.get(badgeID=unique_id, courseID=course)
            duelChallenges = duelChallenges.filter(sendTime__gte=periodic_badge.lastModified)
        elif award_type == 'vc':
            periodicVC = VirtualCurrencyPeriodicRule.objects.get(vcRuleID=unique_id, courseID=course)
            duelChallenges = duelChallenges.filter(sendTime__gte=periodicVC.lastModified)
        elif award_type == 'leaderboard':
            periodic_leaderboard =  LeaderboardsConfig.objects.get(leaderboardID=unique_id, courseID=course)
            duelChallenges = duelChallenges.filter(sendTime__gte=periodic_leaderboard.lastModified)



        
    # Get the total duelChallenges only if they have earned more than 0
    total = len(duelChallenges)
   
    print("Course: {}".format(course))
    print("Student: {}".format(student))
    print("Periodic Variable: {}".format(periodic_variable))
    print("Last Ran: {}".format(last_ran))
    print("Total duelChallenges: {}".format(total))
    if total_only:
        return total
    else:
        return (student, total)

def calculate_duels_sent_and_accepted(info, result_only=False):
    from Students.models import StudentActions, StudentEventLog
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule, LeaderboardsConfig
    
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
    info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']
    
    StudentEventLog = StudentEventLog.objects.filter(course=course, student=student,event=873)
    # If this is not the first time running, only get the StudentEventLog since last ran
    if last_ran:
        studentEventLog = StudentEventLog.filter(timestamp__gte=last_ran)
    else:
        # Set the last ran to equal to the time the rule/badge was created/modified since we don't want to get all the previous StudentEventLog from beginning of time
        if award_type == 'badge':
            periodic_badge = PeriodicBadges.objects.get(badgeID=unique_id, courseID=course)
            studentEventLog = StudentEventLog.filter(timestamp__gte=periodic_badge.lastModified)
        elif award_type == 'vc':
            periodicVC = VirtualCurrencyPeriodicRule.objects.get(vcRuleID=unique_id, courseID=course)
            studentEventLog = StudentEventLog.filter(timestamp__gte=periodicVC.lastModified)
        elif award_type == 'leaderboard':
            periodic_leaderboard =  LeaderboardsConfig.objects.get(leaderboardID=unique_id, courseID=course)
            studentEventLog = StudentEventLog.filter(timestamp__gte=periodic_leaderboard.lastModified)
    
    # Get the total StudentEventLog only if they have earned more than 0
    total = len(studentEventLog) + calculate_duels_sent(info, total_only=True)
   
    print("Course: {}".format(course))
    print("Student: {}".format(student))
    print("Periodic Variable: {}".format(periodic_variable))
    print("Last Ran: {}".format(last_ran))
    print("Total StudentEventLog: {}".format(total))
    return (student, total)

def calculate_duels_won(info, result_only=False, total_only=False):
    from Students.models import Winners
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule, LeaderboardsConfig
    
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
    info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']
    
    winners = Winners.objects.filter(courseID=course, studentID=student)

    # If this is not the first time running, only get the duel wins since last ran
    if last_ran:
        winners = winners.filter(DuelChallengeID__sendTime__gte=last_ran)
    else:
        # Set the last ran to equal to the time the rule/badge was created/modified since we don't want to get all the previous duel wins from beginning of time
        if award_type == 'badge':
            periodic_badge = PeriodicBadges.objects.get(badgeID=unique_id, courseID=course)
            winners = winners.filter(DuelChallengeID__sendTime__gte=periodic_badge.lastModified)
        elif award_type == 'vc':
            periodicVC = VirtualCurrencyPeriodicRule.objects.get(vcRuleID=unique_id, courseID=course)
            winners = winners.filter(DuelChallengeID__sendTime__gte=periodicVC.lastModified)
        elif award_type == 'leaderboard':
            periodic_leaderboard =  LeaderboardsConfig.objects.get(leaderboardID=unique_id, courseID=course)
            winners = winners.filter(DuelChallengeID__sendTime__gte=periodic_leaderboard.lastModified)

    print("Course: {}".format(course))
    print("Student: {}".format(student))
    print("Periodic Variable: {}".format(periodic_variable))
    print("Last Ran: {}".format(last_ran))
    print("Total duelChallenges: {}".format(total))
    if total_only:
        return winners.count()
    else:
        return (student, winners.count())

def calculate_unique_warmups(info, result_only=False):
    ''' This calculates the number of unique Warm-up challenges the student has completed
        with a score greater than or equal to 70%.
    '''
    print("Calculating Unique Warmups with a Score > 70%") 
    from Instructors.models import Challenges
    from Students.models import StudentChallenges
    from decimal import Decimal
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule
    
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']

    # Check aganist only Warm-up challenges
    challenges = Challenges.objects.filter(courseID=course, isGraded=False)
    # The amount of unique Warm-up challenges with a score greater than 60%
    unique_warmups = 0

    # If theere are no Warm-up challenges then we just say the student didn't complete anything
    if not challenges.exists():
        return (student, unique_warmups)
    
    # If this is first time running, set the last ran to equal to the time the rule/badge was created/modified since we don't want to get all the previous challenges from beginning of time
    if not last_ran:
        if award_type == 'badge':
            periodic_badge = PeriodicBadges.objects.get(badgeID=unique_id, courseID=course)
            last_ran = periodic_badge.lastModified
        elif award_type == 'vc':
            periodicVC = VirtualCurrencyPeriodicRule.objects.get(vcRuleID=unique_id, courseID=course)
            last_ran = periodicVC.lastModified
        elif award_type == 'leaderboard':
            periodic_leaderboard =  LeaderboardsConfig.objects.get(leaderboardID=unique_id, courseID=course)
            last_ran = periodic_leaderboard.lastModified
    
    for challenge in challenges:
        # Get the student completed Warm-up challenges (Endtimestamp is not null if it is complete)
        studentChallenges = StudentChallenges.objects.filter(courseID=course, studentID=student, challengeID=challenge.challengeID).exclude(endTimestamp__isnull=True)
        # Only get the student challenges since last ran
        if last_ran:
            studentChallenges = studentChallenges.filter(endTimestamp__gte=last_ran)

        if studentChallenges.exists():
            print("Student Challenges: {}".format(studentChallenges))
            # Get the total possible score (Note: total score is Decimal type) for this challenge
            total_score_possible = challenge.getCombinedScore()
            # Get the highest score out of the student attempts for this challenge (Note: test score is Decimal type)
            highest_score = max([warmup.getScore() for warmup in studentChallenges])
            # If the total possible score is not set then skip this Warm-up challenge
            if total_score_possible <= 0:
                continue
            # Calculate the percentage
            percentage = (highest_score/total_score_possible) * Decimal(100)
            # Say this challenge is counted for if the student score percentage is greater than 70%
            if percentage >= Decimal(70.0):
                unique_warmups += 1

    print("Course: {}".format(course))
    print("Student: {}".format(student))
    print("Periodic Variable: {}".format(periodic_variable))
    print("Last Ran: {}".format(last_ran))
    print("Unique Warm-ups: {}".format(unique_warmups))

    return (student, unique_warmups)
def streakProvider(unique_id, course, student, current_timezone, streakTypeNum):
    from Students.models import StudentStreaks
    if StudentStreaks.objects.filter(courseID=course.courseID, studentID=student, streakType=streakTypeNum).exists():
        studentStreak = StudentStreaks.objects.filter(courseID=course.courseID, studentID=student)[0]
    else:
        studentStreak = StudentStreaks()
        studentStreak.studentID = student
        studentStreak.courseID = course
        studentStreak.streakStartDate = current_localtime(tz=current_timezone).strftime("%Y-%m-%d")
        studentStreak.streakType = streakTypeNum
        studentStreak.objectID = unique_id
        studentStreak.currentStudentStreakLength = 0
        studentStreak.save()
    return studentStreak

def calculate_student_attendance_streak(info, result_only=False):
    print("Calculating student_attendance_streak") 
    #this one is best ran with daily time period
    #weekly will work but cause it to ignore the extra attendance days unless set up properly.
    #should be set before the start of a week, week defined as 7 days. 
    from Students.models import StudentStreaks, StudentAttendance
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule, CourseConfigParams
    from Badges.models import AttendanceStreakConfiguration
    import ast
    
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']

    threshold = 0
    resetStreak = False
    streakTypeNum = None
    #if detemine which type of award this is and obtain thresholds and resetStreak booleans
    if award_type == 'badge':
        if PeriodicBadges.objects.filter(badgeID=unique_id).exists():
            periodicBadge = PeriodicBadges.objects.filter(badgeID=unique_id)[0]
            threshold = periodicBadge.threshold
            resetStreak = periodicBadge.resetStreak
            streakTypeNum = 0
    elif award_type == 'vc':
        if VirtualCurrencyPeriodicRule.objects.filter(vcRuleID=unique_id).exists():
            periodicVC = VirtualCurrencyPeriodicRule.objects.filter(vcRuleID=unique_id)[0]
            threshold = periodicVC.threshold
            resetStreak = periodicVC.resetStreak
            streakTypeNum = 1

    studentStreak = streakProvider(unique_id, course, student, current_timezone, streakTypeNum)
        
    
    # Get the attendance for this student
    if AttendanceStreakConfiguration.objects.filter(courseID=course.courseID).exists():
        print("attendance exists")
        
        #determine the days of class for our entire class
        streak = AttendanceStreakConfiguration.objects.filter(courseID=course.courseID)[0]
        excluded_Dates = streak.daysDeselected
        streakDays = ast.literal_eval(streak.daysofClass)
        streakDays = [int(i) for i in streakDays]
        streak_calendar_days = []
        ccparams = CourseConfigParams.objects.get(courseID=course.courseID)
        d = ccparams.courseStartDate
        while d <= ccparams.courseEndDate:
            if d.weekday() in streakDays:
                streak_calendar_days.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        events = []
        filteredStreakDays = []
        for date in streak_calendar_days:
            if date not in excluded_Dates:
                filteredStreakDays.append(date)
        isTodayStreakDay = False
        if current_localtime(tz=current_timezone).strftime("%Y-%m-%d") in filteredStreakDays:
            isTodayStreakDay = True
            

    student_total = 0
    # Cases when threshold is passed as max or avg or as number
    # Need to calculate the avg of all students totals and find max of all students

    students = StudentRegisteredCourses.objects.filter(courseID=course, studentID__isTestStudent=False)
    max_total = 0
    avg_total = 0
    for s in students:
        stud = s.studentID
        total = 0
        if StudentAttendance.objects.filter(courseID=course.courseID, studentID=stud, timestamp=current_localtime(tz=current_timezone).strftime("%Y-%m-%d")).exists():
            if StudentStreaks.objects.filter(courseID=course.courseID, studentID=stud, streakType=0).exists():
                streak = StudentStreaks.objects.filter(courseID=course.courseID, studentID=stud)[0]
                total = streak.currentStudentStreakLength + 1
                
        # Set max
        if total > max_total:
            max_total = total

        # Sum to find avg
        avg_total += total

        # Set the student total that this calculation should be run for
        if stud == student:
            student_total = total
        
    
    avg_total = avg_total / len(students)

    # Determine the threshold value 
    if threshold == 'max':
        threshold = max_total
    elif threshold == 'avg':
        threshold = avg_total
    else:
        threshold = int(threshold)

    if isTodayStreakDay:
        
        if StudentAttendance.objects.filter(courseID=course.courseID, studentID=student, timestamp=current_localtime(tz=current_timezone).strftime("%Y-%m-%d")).exists():
            studentStreak.currentStudentStreakLength += 1
            student_total = studentStreak.currentStudentStreakLength
            
            #if total is larger than streak and we want to NOT reset streak
            if student_total > threshold and resetStreak:
                studentStreak.currentStudentStreakLength = 0
                student_total = threshold
            elif student_total > threshold and not resetStreak:
                if student_total % threshold == 0:
                    student_total = threshold
                else:
                    #student_total is set as remainder of current streak length and threshold
                    student_total %= threshold
            elif student_total == threshold and resetStreak:
                studentStreak.currentStudentStreakLength = 0
                student_total = threshold
            elif student_total == threshold and not resetStreak:
                student_total = threshold
                
        else:
            studentStreak.currentStudentStreakLength = 0
            student_total = 0
                        
        studentStreak.save()
    print("Course: {}".format(course))
    print("Student: {}".format(student))
    print("Periodic Variable: {}".format(periodic_variable))
    print("Last Ran: {}".format(last_ran))
    print("Total Earnings: {}".format(student_total))

    return (student, student_total)

def calculate_student_challenge_streak(info, result_only=False):
    print("Calculating student challenge streak") 
    from Students.models import StudentStreaks, StudentChallenges, StudentRegisteredCourses
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule

    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']
               
    threshold = 0
    resetStreak = False 
    streakTypeNum = None
    #if detemine which type of award this is and obtain thresholds and resetStreak booleans
    if award_type == 'badge':
        if PeriodicBadges.objects.filter(badgeID=unique_id).exists():
            periodicBadge = PeriodicBadges.objects.filter(badgeID=unique_id)[0]
            threshold = periodicBadge.threshold
            resetStreak = periodicBadge.resetStreak
            streakTypeNum = 0
    elif award_type == 'vc':
        if VirtualCurrencyPeriodicRule.objects.filter(vcRuleID=unique_id).exists():
            periodicVC = VirtualCurrencyPeriodicRule.objects.filter(vcRuleID=unique_id)[0]
            threshold = periodicVC.threshold
            resetStreak = periodicVC.resetStreak
            streakTypeNum = 1
     
    studentStreak = streakProvider(unique_id, course, student, current_timezone, streakTypeNum)  

    student_total = 0
    # Cases when threshold is passed as max or avg or as number
    # Need to calculate the avg of all students totals and find max of all students

    students = StudentRegisteredCourses.objects.filter(courseID=course.courseID, studentID__isTestStudent=False)
    max_total = 0
    avg_total = 0
    for s in students:
        stud = s.studentID
        total = 0
        if last_ran:
            challengeCount = len(StudentChallenges.objects.filter(studentID=stud, courseID=course.courseID, endTimestamp__range=(current_localtime(tz=current_timezone).strftime("%Y-%m-%d"), last_ran.strftime("%Y-%m-%d"))))
        else:
            challengeCount = len(StudentChallenges.objects.filter(studentID=stud, courseID=course.courseID))
        #figure out how many challenges have been completed by the student
        if StudentStreaks.objects.filter(courseID=course.courseID, studentID=stud, streakType=0).exists():
            streak = StudentStreaks.objects.filter(courseID=course.courseID, studentID=stud)[0]
            streak.currentStudentStreakLength += challengeCount
            total = streak.currentStudentStreakLength
        else:
            total = challengeCount
        
        # Set max
        if total > max_total:
            max_total = total

        # Sum to find avg
        avg_total += total

        # Set the student total that this calculation should be run for
        if stud == student:
            student_total = total
    
    avg_total = avg_total / len(students)

    # Determine the threshold value 
    if threshold == 'max':
        threshold = max_total
    elif threshold == 'avg':
        threshold = avg_total
    else:
        threshold = int(threshold)



    #if total is larger than streak and we want to NOT reset streak
    if student_total > threshold and resetStreak:
        studentStreak.currentStudentStreakLength = 0
        student_total = threshold
    elif student_total > threshold and not resetStreak:
        if student_total % threshold == 0:
            student_total = threshold
        else:
            #total is set as remainder of current streak length and threshold
            student_total %= threshold
    elif student_total == threshold and resetStreak:
        studentStreak.currentStudentStreakLength = 0
        student_total = threshold
    elif student_total == threshold and not resetStreak:
        student_total = threshold
        
    # Check if required values are None. see https://stackoverflow.com/questions/9991708/django-south-migration-doesnt-set-default
    if studentStreak.streakType is None:
        studentStreak.streakType = 0
    if studentStreak.objectID is None:
        studentStreak.objectID = 0
    if studentStreak.currentStudentStreakLength is None:
        studentStreak.currentStudentStreakLength = 0
    studentStreak.save()
    
    print("Course: {}".format(course))
    print("Student: {}".format(student))
    print("Periodic Variable: {}".format(periodic_variable))
    print("Last Ran: {}".format(last_ran))
    print("Total Earnings: {}".format(student_total))

    
    return (student, student_total)

def getPercentageScoreForStudent(challengeID, student, percentage, last_ran, current_timezone):
    from Students.models import StudentStreaks, StudentChallenges
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule
    from Instructors.models import Challenges
    challengeTotalScore = 0
    studentScores = []
    filteredStudentScores = []
    maxStudentScore = 0

    challengeID = challengeID.challengeID
    print("percentage", percentage)
    if Challenges.objects.filter(challengeID= challengeID).exists():
        challengeTotalScore = Challenges.objects.filter(challengeID= challengeID)[0].totalScore
    
    if StudentChallenges.objects.filter(challengeID=challengeID, studentID=student, endTimestamp__range=[last_ran.strftime("%Y-%m-%d"), current_localtime(tz=current_timezone).strftime("%Y-%m-%d %H:%M:%S")]).exists():
        studentChallenges = StudentChallenges.objects.filter(challengeID=challengeID, studentID=student)
        for studentChallenge in studentChallenges:
            studentScores.append(float((studentChallenge.testScore/challengeTotalScore)))
        filteredStudentScores = list(filter(lambda x: x >= percentage, studentScores))
        print("filteredStudentScores", filteredStudentScores)
    if filteredStudentScores:
        return 1
    else:
        return 0

def calculate_student_challenge_streak_for_percentage(info, percentage, result_only):
    print("Calculating student challenge >= streak") 
    from Students.models import StudentStreaks, StudentChallenges, StudentRegisteredCourses
    from Badges.models import PeriodicBadges, VirtualCurrencyPeriodicRule
    from Instructors.models import Challenges

    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']

    percentage = percentage *.01
    # Check aganist only Warm-up challenges
    challenges = Challenges.objects.filter(courseID=course, isGraded=False)
    # The amount of unique Warm-up challenges with a score greater than 60%
    unique_warmups = 0
    
    # If this is first time running, set the last ran to equal to the time the rule/badge was created/modified since we don't want to get all the previous challenges from beginning of time
    if last_ran == None:
        if award_type == 'badge':
            periodic_badge = PeriodicBadges.objects.get(badgeID=unique_id, courseID=course)
            last_ran = periodic_badge.lastModified
        elif award_type == 'vc':
            periodicVC = VirtualCurrencyPeriodicRule.objects.get(vcRuleID=unique_id, courseID=course)
            last_ran = periodicVC.lastModified
           
    threshold = 0
    resetStreak = False
    streakTypeNum = None
    #if detemine which type of award this is and obtain thresholds and resetStreak booleans
    if award_type == 'badge':
        if PeriodicBadges.objects.filter(badgeID=unique_id).exists():
            periodicBadge = PeriodicBadges.objects.filter(badgeID=unique_id)[0]
            threshold = periodicBadge.threshold
            resetStreak = periodicBadge.resetStreak
            streakTypeNum = 0
    elif award_type == 'vc':
        if VirtualCurrencyPeriodicRule.objects.filter(vcRuleID=unique_id).exists():
            periodicVC = VirtualCurrencyPeriodicRule.objects.filter(vcRuleID=unique_id)[0]
            threshold = periodicVC.threshold
            resetStreak = periodicVC.resetStreak
            streakTypeNum = 1
     
    studentStreak = streakProvider(unique_id, course, student, current_timezone, streakTypeNum)   
                
    student_total = 0
    # Cases when threshold is passed as max or avg or as number
    # Need to calculate the avg of all students totals and find max of all students

    students = StudentRegisteredCourses.objects.filter(courseID=course, studentID__isTestStudent=False)
    max_total = 0
    avg_total = 0
    for s in students:
        stud = s.studentID
        total = 0
        studentChallengeIDs = []
        maxScores = []
        studentChallenges = StudentChallenges.objects.filter(studentID=stud, courseID=course.courseID, endTimestamp__range=[last_ran.strftime("%Y-%m-%d"), current_localtime(tz=current_timezone).strftime("%Y-%m-%d %H:%M:%S")])
        print("StudentChallenges", studentChallenges)
        for challenge in studentChallenges:
            if getPercentageScoreForStudent(challenge.challengeID, stud, percentage, last_ran, current_timezone):
                total += 1
                studentStreak = streakProvider(unique_id, course, stud, current_timezone, streakTypeNum) 
                studentStreak.currentStudentStreakLength += 1
                studentStreak.save()
            
        print("current total for student", total)
        
        # Set max
        if total > max_total:
            max_total = total

        # Sum to find avg
        avg_total += total

        # Set the student total that this calculation should be run for
        if stud == student:
            student_total = total
            studentStreak = streakProvider(unique_id, course, student, current_timezone, streakTypeNum)
    
    avg_total = avg_total / len(students)

    # Determine the threshold value 
    if threshold == 'max':
        threshold = max_total
    elif threshold == 'avg':
        threshold = avg_total
    else:
        threshold = int(threshold)

    #if total is larger than streak and we want to NOT reset streak
    if student_total >= threshold and resetStreak:
        studentStreak.currentStudentStreakLength = 0
        student_total = threshold
    elif student_total > threshold and not resetStreak:
        if studentStreak.currentStudentStreakLength % threshold == 0:
            student_total = threshold
        else:
            #total is set as remainder of current streak length and threshold
            student_total %= threshold
    elif student_total == threshold and not resetStreak:
        student_total = threshold
    studentStreak.save()
    
    print("Course: {}".format(course))
    print("Student: {}".format(student))
    print("Periodic Variable: {}".format(periodic_variable))
    print("Last Ran: {}".format(last_ran))
    print("Total Earnings: {}".format(student_total))

    
    return (student, student_total)

#def calculate_warmup_challenge_greater_or_equal_to_70(info, result_only=False):
 #   return calculate_student_challenge_streak_for_percentage(info, 70, result_only)

def calculate_warmup_challenge_greater_or_equal_to_80(info, result_only=False):
    return calculate_student_challenge_streak_for_percentage(info, 80, result_only)
                                                             
def calculate_warmup_challenge_greater_or_equal_to_40(info, result_only=False):
    return calculate_student_challenge_streak_for_percentage(info, 40, result_only)

def calculate_student_xp_rankings(info, result_only=False):
    from Students.models import StudentRegisteredCourses
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']

    student_reg_course = StudentRegisteredCourses.objects.get(courseID=course, studentID=student)
    
    # result = studentScore(student, course, unique_id, last_ran=last_ran, result_only=result_only, gradeWarmup=False, gradeSerious=False, gradeActivity=False)
    xp = student_reg_course.xp
    return (student, xp)
    
def calculate_warmup_rankings(info, result_only=False):
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']

    result = studentScore(student, course, unique_id, last_ran=last_ran, result_only=result_only, gradeSerious=False, gradeActivity=False)
    xp = result['xp']
    return (student, xp)
    
def calculate_serious_challenge_rankings(info, result_only=False):
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']

    result = studentScore(student, course, unique_id, last_ran=last_ran, result_only=result_only, gradeWarmup=False, gradeActivity=False)
    xp = result['xp']
    return (student, xp)
    
def calculate_serious_challenge_and_activity_rankings(info, result_only=False):
    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']

    result = studentScore(student, course, unique_id , last_ran=last_ran, result_only=result_only, gradeWarmup=False, gradeSerious=False)
    xp = result['xp']
    return (student, xp)

def studentScore(for_student, course, unique_id, result_only=False, last_ran=None, gradeWarmup=True, gradeSerious=True, gradeActivity=True, gradeSkills=True, for_class=False):
    
    from Badges.models import CourseConfigParams, LeaderboardsConfig, PeriodicBadges, VirtualCurrencyPeriodicRule
    from Instructors.models import Challenges, Activities, CoursesSkills, Skills, ActivitiesCategory
    from Instructors.constants import uncategorized_activity
    from Students.models import StudentChallenges, StudentActivities, StudentCourseSkills, StudentRegisteredCourses

    result = {}

    xp = 0  
    xpWeightSP = 0
    xpWeightSChallenge = 0
    xpWeightWChallenge = 0
    xpWeightAPoints = 0
    ccparamsList = CourseConfigParams.objects.filter(courseID=course)
    
    # If result only, we only want to search from the start of the course
    # else, we will search based on howFarBack (see below)
    if result_only:
        startOfTime = True
    else:
        date_time = last_ran 
        
        if not date_time:
            periodic_leaderboard =  LeaderboardsConfig.objects.filter(leaderboardID=unique_id, courseID=course).first()
            periodic_badge = PeriodicBadges.objects.filter(badgeID=unique_id, courseID=course).first()
            periodicVC = VirtualCurrencyPeriodicRule.objects.filter(vcRuleID=unique_id, courseID=course).first()
            if periodic_leaderboard:
                backwardsTime = periodic_leaderboard.howFarBack
                
                if backwardsTime == 1500:
                    date_time = date.today() - timedelta(1)
                    
                elif backwardsTime == 1501:
                    date_time = date.today() - timedelta(7)

            elif periodic_badge == 'badge':
                date_time = periodic_badge.lastModified
            elif periodicVC == 'vc':
                date_time = periodicVC.lastModified
            
        startOfTime = False
        
    
    
    # print("Course: {}".format(course))
    # print("Student: {}".format(for_student))
    
    # Specify if the xp should be calculated based on max score or first attempt
    xpSeriousMaxScore = True 
    xpWarmupMaxScore = True
    challengeClassmates = False
    if ccparamsList.exists():
        cparams = ccparamsList[0]
        xpWeightSP = cparams.xpWeightSP
        xpWeightSChallenge = cparams.xpWeightSChallenge
        xpWeightWChallenge = cparams.xpWeightWChallenge
        xpWeightAPoints = cparams.xpWeightAPoints
        xpSeriousMaxScore = cparams.xpCalculateSeriousByMaxScore 
        xpWarmupMaxScore = cparams.xpCalculateWarmupByMaxScore

        if cparams.classmatesChallenges:
            challengeClassmates = True
    
    if for_class:
        students = StudentRegisteredCourses.objects.filter(courseID= course, studentID__isTestStudent=False)
    else:
        students = StudentRegisteredCourses.objects.filter(courseID= course, studentID=for_student)
       
    if gradeSerious:
        # SERIOUS CHALLENGES
        # Get the earned points
        earnedSeriousChallengePoints = 0 
        totalPointsSeriousChallenges = 0
        
        chall_name = []
        score = []
        total = []
        challavg = []

        courseChallenges = Challenges.objects.filter(courseID=course, isGraded=True, isVisible=True).order_by('challengePosition').values('challengeID', 'challengeName', 'totalScore', 'manuallyGradedScore')
        for challenge in courseChallenges:
            count = 0
            classAvgChallengeScore = 0
            for student in students:
                if not startOfTime and date_time:
                    seriousChallengeAttempts = StudentChallenges.objects.filter(studentID=student.studentID, courseID=course,challengeID=challenge['challengeID'], endTimestamp__gte=date_time).order_by('studentChallengeID')
                else:
                    seriousChallengeAttempts = StudentChallenges.objects.filter(studentID=student.studentID, courseID=course,challengeID=challenge['challengeID']).order_by('studentChallengeID')

                # Ignore challenges that have invalid total scores
                if seriousChallengeAttempts.exists() and challenge['totalScore'] < 0:
                    continue
                
                # Get the scores for this challenge then either add the max score
                # or the first score to the earned points variable
                max_score = 0
                max_basic_score = 0
                first_attempt = -1
                for attempt in seriousChallengeAttempts:
                    value = float(attempt.getScoreWithBonus())
                    if first_attempt == -1:
                        first_attempt = value

                    max_score = max(max_score, value)
                    max_basic_score = max(max_basic_score, float(attempt.getScore()))

                # Setup data for rendering this challenge in html (bar graph stuff)
                if first_attempt != -1:
                    count += 1
                    classAvgChallengeScore += max_basic_score

                    if xpSeriousMaxScore:                           
                        earnedSeriousChallengePoints += max_score   
                        score.append(max_score)        
                    else:
                        earnedSeriousChallengePoints += first_attempt
                        score.append(first_attempt)
                    chall_name.append(challenge['challengeName'])
                        
                    # Total possible points for challenge
                    combined_score = challenge['totalScore'] + challenge['manuallyGradedScore']
                    total.append(combined_score)
                    totalPointsSeriousChallenges += float(combined_score) if combined_score > 0 else 1
             
            if count > 0:
                classAvgChallengeScore = classAvgChallengeScore/count
                for _ in range(count):
                    challavg.append(classAvgChallengeScore)
        
        # Weighting the total serious challenge points to be used in calculation of the XP Points  
        weightedSeriousChallengePoints = earnedSeriousChallengePoints * xpWeightSChallenge / 100
        # logger.debug(f"total score points serious {weightedSeriousChallengePoints}")
        
        
        result['challenge_range'] = list(zip(range(1, len(chall_name)+1), chall_name, score, total))
        result['challengeWithAverage_range'] = list(zip(range(1, len(chall_name)+1), chall_name, score, total, challavg))

        result['weightedSeriousChallengePoints'] = weightedSeriousChallengePoints
        result['earnedSeriousChallengePoints'] = earnedSeriousChallengePoints
        result['totalPointsSeriousChallenges'] = totalPointsSeriousChallenges

    
    if gradeWarmup:
        # WARMUP CHALLENGES
        # Get the earned points
        earnedWarmupChallengePoints = 0 
        totalWCPossiblePoints = 0
        totalWCEarnedPoints = 0

        chall_Name = []
        total = []
        noOfAttempts = []
        warmUpMaxScore = []
        warmUpMinScore = []
        warmUpSumScore = []
        warmUpSumPossibleScore = []
        
        courseChallenges = Challenges.objects.filter(courseID=course, isGraded=False).values('challengeID', 'challengeName', 'totalScore', 'manuallyGradedScore')
        for challenge in courseChallenges:
            for student in students:
                if not startOfTime and date_time:
                    warmupChallengeAttempts = StudentChallenges.objects.filter(studentID=student.studentID, courseID=course, challengeID=challenge['challengeID'], endTimestamp__gte=date_time)
                else:
                    warmupChallengeAttempts = StudentChallenges.objects.filter(studentID=student.studentID, courseID=course,challengeID=challenge['challengeID'])

                # Ignore challenges that have invalid total scores
                if warmupChallengeAttempts.exists() and challenge['totalScore'] < 0:
                    continue

                # Get the scores for this challenge then either add the max score
                # or the first score to the earned points variable
                scores = []
                first_attempt = -1

                for attempt in warmupChallengeAttempts:
                    value = float(attempt.testScore)
                    if first_attempt == -1:
                        first_attempt = value

                    scores.append(value)

                # Setup data for rendering this challenge in html (bar graph stuff)
                if first_attempt != -1:
                    max_score = max(scores)

                    if xpWarmupMaxScore:                          
                        earnedWarmupChallengePoints += max_score
                    else:
                        earnedWarmupChallengePoints += first_attempt

                    chall_Name.append(challenge['challengeName'])
                    # total possible points for challenge
                    total.append(challenge['totalScore'])
                    warmup_attempts_count = warmupChallengeAttempts.count()
                    noOfAttempts.append(warmup_attempts_count)
                    # Total possible points for all attempts for this challenge
                    warmUpSumPossibleScore.append(challenge['totalScore'] * warmup_attempts_count)
                    totalWCPossiblePoints += challenge['totalScore'] * warmup_attempts_count
                    
                    warmUpMaxScore.append(max_score)
                    warmUpMinScore.append(min(scores))
                    totalWCEarnedPoints += sum(scores)
                
        # Weighting the total warmup challenge points to be used in calculation of the XP Points  
        weightedWarmupChallengePoints = earnedWarmupChallengePoints * xpWeightWChallenge / 100      # max grade for this challenge

        
        result['warmUpContainerHeight'] = 100+60*len(chall_Name)
        result['studentWarmUpChallenges_range'] = list(zip(range(1, len(
            chall_Name)+1), chall_Name, total, noOfAttempts, warmUpMaxScore, warmUpMinScore))
        result['totalWCEarnedPoints'] = totalWCEarnedPoints
        result['totalWCPossiblePoints'] = totalWCPossiblePoints

        result['weightedWarmupChallengePoints'] = weightedWarmupChallengePoints
        result['earnedWarmupChallengePoints'] = earnedWarmupChallengePoints

    if gradeActivity:
        # ACTIVITIES
        # Get the earned points
        earnedActivityPoints = 0
        totalPointsActivities = 0

        courseActivities = Activities.objects.filter(courseID=course, isGraded=True).values('activityID', 'points', 'category__xpWeight', 'category__name')
        for activity in courseActivities:
            for student in students:
                if not startOfTime and date_time:
                    studentActivity = StudentActivities.objects.filter(studentID=student.studentID, courseID=course, activityID=activity['activityID'], timestamp__gte=date_time)
                else:
                    studentActivity = StudentActivities.objects.filter(studentID=student.studentID, courseID=course, activityID=activity['activityID'])
                
                if studentActivity.exists():
                    xpWeightCategory = 1
                    if activity['category__name'] != uncategorized_activity:
                        xpWeightCategory = float(activity['category__xpWeight'])

                    # Get the scores for this challenge then add the max score
                    # to the earned points variable
                    score = float(studentActivity.first().getScoreWithBonus())
                    earnedActivityPoints += score * xpWeightCategory
                    totalPointsActivities += float(activity['points'])

        # Weighting the total activity points to be used in calculation of the XP Points  
        weightedActivityPoints = earnedActivityPoints * xpWeightAPoints / 100
       
        result['weightedActivityPoints'] = weightedActivityPoints
        result['earnedActivityPoints'] = earnedActivityPoints
        result['totalPointsActivities'] = totalPointsActivities

    if gradeSkills:
        # SKILL POINTS
        # Get the earned points
        earnedSkillPoints = 0

        skill_Name = []                
        skill_Points = []
        skill_ClassAvg = []
        
        cskills = CoursesSkills.objects.filter(courseID=course)
        for sk in cskills:
            
            skill = Skills.objects.get(skillID=sk.skillID.skillID)
            
            userCount = 0
            classAvgSkill = 0
            
            for student in students:
                sp = StudentCourseSkills.objects.filter(studentChallengeQuestionID__studentChallengeID__studentID=student.studentID,skillID = skill)
                # print ("Skill Points Records", sp)
                skill_Name.append(skill.skillName)
                if not sp.exists():  
                    skill_Points.append(0)                     
                else:    
                    # Get the scores for this challenge then add the max score
                    # to the earned points variable                 
                    score = 0
                    for p in sp:
                        score += int(p.skillPoints)
                    
                    earnedSkillPoints += score

                    skill_Points.append(score)
                    userCount += 1
            
            if userCount > 0:
                classAvgSkill = earnedSkillPoints/userCount
                for _ in range(userCount):
                    skill_ClassAvg.append(classAvgSkill)
        
        result['skill_range'] = list(
            zip(range(1, len(skill_Name)+1), skill_Name, skill_Points))
        result['nondefskill_range'] = list(
            zip(range(1, len(skill_Name)+1), skill_Name, skill_Points))
        result['skillWithAverage_range'] = list(
            zip(range(1, len(skill_Name)+1), skill_Name, skill_ClassAvg))

        # Weighting the total skill points to be used in calculation of the XP Points     
        # print("earnedSkillPoints: ", earnedSkillPoints)              
        weightedSkillPoints = earnedSkillPoints * xpWeightSP / 100   

        result['weightedSkillPoints'] = weightedSkillPoints
        result['earnedSkillPoints'] = earnedSkillPoints


    
    
    # Return the xp and/or required variables rounded to 1 decimal place
    xp = 0
    if gradeWarmup:
        xp += weightedWarmupChallengePoints
        # print("warmup ran")
    if gradeSerious:
        xp += weightedSeriousChallengePoints
        # print("serious ran")
    if gradeActivity:
        xp += weightedActivityPoints
    if gradeSkills:
        xp += weightedSkillPoints

    if not gradeSerious and not gradeWarmup and not gradeActivity and not gradeSkills:
        xp += weightedSeriousChallengePoints + weightedWarmupChallengePoints  + weightedActivityPoints + weightedSkillPoints
    
    xp = round(xp, 1)
    result['xp'] = xp
    
    return result

def calculate_number_of_days_logging_in (info, result_only=False):
    ''' This will return the number of days a student has logged in.'''
    from Students.models import StudentEventLog
    from Badges.enums import Event
    #Counter that sums every login event for a particular course with a new Day timestamp
    #return the counter

    course, student, periodic_variable, time_period, last_ran, unique_id, award_type, current_timezone = info['course'], info['student'], \
        info['periodic_variable'], info['time_period'], info['last_ran'], info['unique_id'], info['award_type'], info['timezone']
    #Cannot narrow this down to a login event type because the student may never "officially" log out
    #Instead we look at the day timestamp for any student-generated events
    #Must exclude participationNoted event because it is not generated by the student
    if last_ran:
        eventDates = StudentEventLog.objects.filter(student = student, course = course, timestamp__gte=last_ran).values('timestamp').order_by('timestamp')
    else:
        if award_type == 'badge':
            periodic_badge = PeriodicBadges.objects.get(badgeID=unique_id, courseID=course)
            eventDates = StudentEventLog.objects.filter(student = student, course = course, timestamp__gte=periodic_badge.lastModified)\
                .values('timestamp').order_by('timestamp')
        elif award_type == 'vc':
            periodicVC = VirtualCurrencyPeriodicRule.objects.get(vcRuleID=unique_id, courseID=course)
            eventDates = StudentEventLog.objects.filter(student = student, course = course, timestamp__gte=periodicVC.lastModified)\
                .values('timestamp').order_by('timestamp')
        elif award_type == 'leaderboard':
            periodic_leaderboard =  LeaderboardsConfig.objects.get(leaderboardID=unique_id, courseID=course)
            eventDates = StudentEventLog.objects.filter(student = student, course = course, timestamp__gte=periodic_leaderboard.lastModified)\
                .values('timestamp').order_by('timestamp')
        
    studentEventDates = eventDates.exclude(event = Event.participationNoted)
    #Convert the days to integers to make them easier to compare
    dates = list(map(lambda d: d['timestamp'].toordinal(),studentEventDates))
    previous_day = 0   # This is probably Jan 1, 1 AD and shouldn't match.
    consecutive_days = 0
    for d in dates:
        if (d == previous_day):
            continue #if the days match, skip to the next event date
        elif(d - 1 == previous_day):
            consecutive_days += 1 #increment if the days are one off from each other
        else:
            consecutive_days = 0
        previous_day = d
    return (student, consecutive_days)

def get_or_create_schedule(minute='*', hour='*', day_of_week='*', day_of_month='*', month_of_year='*', tz=settings.TIME_ZONE):
    ''' This will get the crontab schedule if it exists and if not it will create it and return it '''
    from django.conf import settings

    if settings.CURRENTLY_MIGRATING:
        return None
        
    schedules = CrontabSchedule.objects.filter(minute=minute, hour=hour, day_of_week=day_of_week, day_of_month=day_of_month, month_of_year=month_of_year, timezone=tz)
    if schedules.exists():
        if len(schedules) > 1:
            schedule_keep = schedules.first()
            CrontabSchedule.objects.exclude(pk__in=schedule_keep).delete()
            return schedule_keep
        else:
            return schedules.first()
    else:
        schedule = CrontabSchedule.objects.create(minute=minute, hour=hour, day_of_week=day_of_week, day_of_month=day_of_month, month_of_year=month_of_year, timezone=tz)
        return schedule

class TimePeriods:
    ''' TimePeriods enum starting at 1500.
        schedule: crontab of when to run
        datetime: used for range when task should run only once
    '''
    daily = 1500 # Runs every day at midnight
    weekly = 1501 # Runs every Sunday at midnight
    biweekly = 1502 # Runs every two weeks on Sunday at midnight
    beginning_of_time = 1503 # Runs only once
    daily_test = 1599
    minute_test = 1598
    timePeriods = {
        daily:{
            'index': daily,
            'name': 'daily',
            'displayName': 'Daily at Midnight',
            'schedule': lambda zone: get_or_create_schedule(
                        minute='59', hour='23', day_of_week='*', 
                        day_of_month='*', month_of_year='*', tz=zone),
            'datetime': lambda zone: current_localtime(tz=zone) - timedelta(days=1),
        },
        weekly:{
            'index': weekly,
            'name': 'weekly',
            'displayName': 'Weekly on Sundays at Midnight',
            'schedule': lambda zone: get_or_create_schedule(minute="59", hour="23", day_of_week='0', tz=zone),
            'datetime': lambda zone: current_localtime(tz=zone) - timedelta(days=7),
        },
        biweekly:{
            'index': biweekly,
            'name': 'biweekly',
            'displayName': 'Every Two Weeks on Sundays at Midnight',
            'schedule': lambda zone: get_or_create_schedule(minute="59", hour="23", day_of_week='0', tz=zone),
            'datetime': lambda zone: current_localtime(tz=zone) - timedelta(days=14),
        },
        beginning_of_time:{
            'index': beginning_of_time,
            'name': 'beginning_of_time',
            'displayName': 'Only once at Midnight',
            'schedule': lambda zone: get_or_create_schedule(minute="59", hour="23", tz=zone),
            'datetime': lambda zone: None,
        }
    }
    if __debug__:
        timePeriods[daily_test] = {
            'index': daily_test,
            'name': 'daily_test',
            'displayName': 'Every other Day (For Testing)',
            'schedule': lambda zone: get_or_create_schedule(
                        minute='59', hour='23', day_of_week='*', 
                        day_of_month='*', month_of_year='*', tz=zone),
            'datetime': lambda zone: current_localtime(tz=zone) - timedelta(days=2),
        }
        timePeriods[minute_test] = {
            'index': minute_test,
            'name': 'minute_test',
            'displayName': 'Every other Minute (For Testing)',
            'schedule': lambda zone: get_or_create_schedule(
                        minute='*', tz=zone),
            'datetime': lambda zone: current_localtime(tz=zone) - timedelta(minutes=2),
        }
class PeriodicVariables:
    '''PeriodicVariables enum starting at 1400.'''
    
    highest_earner = 1400
    student_warmup_practice = 1401
    unique_warmups = 1402
    xp_ranking = 1403
    warmup_challenges = 1404
    serious_challenge = 1405
    serious_challenges_and_activities = 1406
    attendance_streak = 1407
    challenge_streak = 1408
    warmup_challenge_greater_or_equal_to_40 = 1409
    warmup_challenge_greater_or_equal_to_80 = 1410
    number_of_days_of_unique_warmups_90 = 1411
    number_of_days_of_unique_warmups_70 = 1412
    number_of_days_of_2_unique_warmups_80 = 1413
    callouts_sent = 1414
    duels_sent = 1415
    duels_sent_and_accepted = 1416
    number_of_days_logging_in = 1417
    duels_won = 1418
#    warmup_challenge_greater_or_equal_to_70 = 1419
    periodicVariables = {
        highest_earner: {
            'index': highest_earner,
            'name': 'highest_earner',
            'displayName': 'Highest Virtual Currency Earner',
            'description': 'Calculates the highest earner(s) of students based on the virtual currency they have earned',
            'function': calculate_student_earnings,
        },
        student_warmup_practice: {
            'index': student_warmup_practice,
            'name': 'student_warmup_practice',
            'displayName': 'Number of Warmup Challenges Practiced',
            'description': 'The total amount a student has completed any warmup challenges. Including multiple attempts',
            'function': calculate_student_warmup_practice,
        },
        unique_warmups: {
            'index': unique_warmups,
            'name': 'unique_warmups',
            'displayName': 'Unique Warmup Challenges Completed (>= 70% correct)',
            'description': 'The number of unique warmup challenges a student has completed with a score greater than or equal to 70%. The student score only includes the student challenge score, adjustment, and curve.',
            'function': calculate_unique_warmups,
        },
        xp_ranking: {
            'index': xp_ranking,
            'name': 'xp_ranking',
            'displayName': 'Student Rankings via XP',
            'description': 'Retrieves the XP for all students in a class',
            'function': calculate_student_xp_rankings,
        },
        warmup_challenges: {
            'index': warmup_challenges,
            'name': 'warmup_challenges',
            'displayName': 'Student Rankings via Warmup Challenges',
            'description': 'Retrieves the warmup challenges points for all students in a class',
            'function': calculate_warmup_rankings,
        },
        serious_challenge: {
            'index': serious_challenge,
            'name': 'serious_challenge',
            'displayName': 'Student Rankings via Serious Challenges',
            'description': 'Retrieves the serious challenges points for all students in a class',
            'function': calculate_serious_challenge_rankings,
        },
        serious_challenges_and_activities: {
            'index': serious_challenges_and_activities,
            'name': 'serious_challenges_and_activities',
            'displayName': 'Student Rankings via Serious Challenges and Activities',
            'description': 'Retrieves the serious challenges and activities points for all students in a class',
            'function': calculate_serious_challenge_and_activity_rankings,
        },
        attendance_streak: {
            'index': attendance_streak,
            'name': 'attendance_streak',
            'displayName': 'Student Attendance Streak',
            'description': 'Retrieves the student attendance streak of the number of days marked as present',
            'function': calculate_student_attendance_streak,
        },
        warmup_challenge_greater_or_equal_to_80: {
            'index': warmup_challenge_greater_or_equal_to_80,
            'name': 'warmup_challenge_gte_80_by_day',
            'displayName': 'Warmup Challenge Streak Score >= 80% over a period of time',
            'description': 'The student Warmup Challenge Streak Score that is greater than or equal to 80% over a period of time',
            'function': calculate_warmup_challenge_greater_or_equal_to_80,
        },
        warmup_challenge_greater_or_equal_to_40: {
            'index': warmup_challenge_greater_or_equal_to_40,
            'name': 'warmup_challenge_gte_40_by_day',
            'displayName': 'Warmup Challenge Streak Score >= 40% over a period of time',
            'description': 'The student Warmup Challenge Streak Score that is greater than or equal to 40% over a period of time',
            'function': calculate_warmup_challenge_greater_or_equal_to_40,
        },
        challenge_streak: {
            'index': challenge_streak,
            'name': 'challenge_streak',
            'displayName': 'Challenge Streak',
            'description': 'The number of challenges a student has completed while the student has a streak',
            'function': calculate_student_challenge_streak,
        },
        number_of_days_of_unique_warmups_90: {
            'index': number_of_days_of_unique_warmups_90,
            'name': 'number_of_days_of_unique_warmups_90',
            'displayName': 'Number Of Days Of Unique Warmup Challenges Score >= 90%',
            'description': 'The number of days of unique warmup challenges students completed with a score equal or greater than 90%. The student scores only includes the student score, adjustment, and curve.',
            'function': calculate_number_of_days_of_unique_warmups_greater_than_90,
        },
        number_of_days_of_unique_warmups_70: {
            'index': number_of_days_of_unique_warmups_70,
            'name': 'number_of_days_of_unique_warmups_70',
            'displayName': 'Number Of Days Of Unique Warmup Challenges Score >= 70%',
            'description': 'The number of days of unique warmup challenges students completed with a score equal or greater than 70%. The student scores only includes the student score, adjustment, and curve.',
            'function': calculate_number_of_days_of_unique_warmups_greater_than_70,
        },
        number_of_days_of_2_unique_warmups_80: {
            'index': number_of_days_of_2_unique_warmups_80,
            'name': 'number_of_days_of_2_unique_warmups_80',
            'displayName': 'Number Of Days Of 2 Unique Warmup Challenges Score >= 80%',
            'description': 'The number of days of 2 unique warmup challenges students completed with a score equal or greater than 80%. The student scores only includes the student score, adjustment, and curve.',
            'function': calculate_number_of_days_of_2_unique_warmups_greater_than_80,
        },
        callouts_sent:{
            'index': callouts_sent,
            'name': 'callouts_sent',
            'displayName': 'Callouts Sent',
            'description': 'The total number of callouts sent by the student',
            'function': calculate_callouts_sent,
        },
        duels_sent: {
            'index': duels_sent,
            'name': 'duels_sent',
            'displayName': 'Duels Sent',
            'description': 'The total number of duels sent by the student',
            'function': calculate_duels_sent,
        },
        duels_sent_and_accepted: {
            'index': duels_sent_and_accepted,
            'name': 'duels_sent_and_accepted',
            'displayName': 'Duels Sent and Accepted',
            'description': 'The combined total of duels sent and duels accepted by the student',
            'function': calculate_duels_sent_and_accepted,
        },
        number_of_days_logging_in: {
            'index': number_of_days_logging_in,
            'name': 'number_of_days_logging_in',
            'displayName': 'Number of Continuous Days Logged In',
            'description': 'The number of continuous days the student has been active in OneUp',
            'function': calculate_number_of_days_logging_in,
        },
        duels_won: {
            'index': duels_won,
            'name': 'duels_won',
            'displayName': 'Duels Won',
            'description': 'The total number of duels won by the student',
            'function': calculate_duels_won,
        },
    }

if __debug__:
    # Check for mistakes in the periodicVariables enum, such as duplicate id numbers
    periodic_variable_fields = ['index','name','displayName','description','function']
    
    periodic_variable_names = [pv for pv in PeriodicVariables.__dict__ if pv[:1] != '_' and pv != 'periodicVariables']
    periodic_variable_set = set()
    for periodic_variable_name in periodic_variable_names:
        periodic_variable_number = PeriodicVariables.__dict__[periodic_variable_name]
        periodic_variable_set.add(periodic_variable_number)
        
        assert periodic_variable_number in PeriodicVariables.periodicVariables, "Periodic variable number created without corresponding structure in periodicVariables dictionary.  %s = %i " % (periodic_variable_name, periodic_variable_number)
       
        dictEntry = PeriodicVariables.periodicVariables[periodic_variable_number]
        
        for field in periodic_variable_fields:
            assert field in dictEntry, "Periodic variable structure missing expected field.  %s missing %s" % (periodic_variable_name,field)
        
        assert ' ' not in dictEntry['name'], "Periodic variable structure name field must not have spaces.  (%s)" % (periodic_variable_name)

        assert periodic_variable_number == dictEntry['index'], "Periodic variable structure index field must match periodic variable number. %s -> %i != %i" % (periodic_variable_name, periodic_variable_number, dictEntry['index'])

    assert len(periodic_variable_names) == len(periodic_variable_set), "Two periodic variables have the same number."
