import pprint
import re
from datetime import datetime
from decimal import Decimal
from time import time
import pytz

from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render
from django.utils import timezone

from Badges.conditions_util import (databaseConditionToJSONString,
                                    setUpContextDictForConditions)
from Badges.enums import ObjectTypes
from Badges.models import CourseConfigParams
from Badges.tasks import create_due_date_process
from Instructors.constants import unlimited_constant, unspecified_topic_name
from Instructors.models import (
    Answers, Challenges, ChallengesQuestions, CorrectAnswers, CoursesTopics,
    MatchingAnswers, StaticQuestions)
from Instructors.questionTypes import QuestionTypes, questionTypeFunctions
from Instructors.views import challengeListView
from Instructors.views.utils import (
    addTopicsToChallenge, autoCompleteTopicsToJson, current_localtime,
    datetime_to_local, datetime_to_selected, extractTags,
    getTopicsForChallenge, initialContextDict, saveTags, str_datetime_to_local)
from oneUp.ckeditorUtil import config_ck_editor
from oneUp.decorators import instructorsCheck
from oneUp.logger import logger
from Students.models import TeamChallenges
pp = pprint.PrettyPrinter(indent=4)


@login_required
@user_passes_test(instructorsCheck, login_url='/oneUp/students/StudentHome', redirect_field_name='')
def challengeCreateView(request):

    # Request the context of the request.
    # The context contains information such as the client's machine details, for example.
    context_dict, currentCourse = initialContextDict(request)

    context_dict = setUpContextDictForConditions(
        context_dict, currentCourse, None)

    questionObjects = []
    qlist = []
    topic_ID = []
    topic_Name = []

    context_dict['isVisible'] = True
    context_dict['displayCorrectAnswer'] = True

    string_attributes = ['challengeName', 'challengeDifficulty',  # 'isGraded','challengeCategory', 'challengeDifficulty'
                         'numberAttempts', 'timeLimit', 'challengePassword', 'manuallyGradedScore'
                         ]

    # Fetch the topics for this course from the database.
    course_topics = CoursesTopics.objects.filter(courseID=currentCourse)
    for ct in course_topics:
        topic_ID.append(ct.topicID.topicID)
        topic_Name.append(ct.topicID.topicName)

    unspecified_topic = CoursesTopics.objects.get(
        courseID=currentCourse, topicID__topicName=unspecified_topic_name).topicID

    context_dict['topicsAuto'], context_dict['createdTopics'] = autoCompleteTopicsToJson(
        currentCourse)

    if request.method == "POST":
        logger.debug("[POST] " + str(context_dict))
        # Check whether a new challenge or editing an existing challenge
       
        if request.POST['challengeID']:
            challenge = Challenges.objects.get(
                pk=int(request.POST['challengeID']))
        else:
            # Create a NEW Challenge
            challenge = Challenges()
            challenge.courseID = currentCourse
           
        # get isGraded       
        isGraded = str(request.POST.get('isGraded', 'false'))
        print("isGraded post")
        print(isGraded)

        # get isVisible
        isVisible = str(request.POST.get('isVisible', 'false'))

        # get randomization GGM
        isRandomized = str(request.POST.get('randomizeProblems', 'false'))
        
 
        # get difficulty
        if('challengeDifficulty' in request.POST):
            challenge.challengeDifficulty = request.POST['challengeDifficulty']
        else:
            challenge.challengeDifficulty = ''

        displayCorrectAnswer = str(
            request.POST.get('displayCorrectAnswer', 'false'))
        displayCorrectAnswerFeedback = str(
            request.POST.get('displayCorrectAnswerFeedback', 'false'))
        displayIncorrectAnswerFeedback = str(
            request.POST.get('displayIncorrectAnswerFeedback', 'false'))
        try:
            challenge.curve = Decimal(request.POST.get("curve", 0))
        except:
            challenge.curve = Decimal(0)

        # Copy all strings from POST to database object.
        for attr in string_attributes:
            if(attr in request.POST):
                setattr(challenge, attr, request.POST[attr])

        try:
            challenge.manuallyGradedScore = Decimal(
                request.POST.get("manuallyGradedScore", 0))
        except:
            challenge.manuallyGradedScore = Decimal(0)

        # get the logged in user for an author
        if request.user.is_authenticated:
            challenge.challengeAuthor = request.user.username
        else:
            challenge.challengeAuthor = ""

        # only empty strings return false when converted to boolean
        if isGraded == str("false"):
            isGraded = ""
        challenge.isGraded = bool(isGraded)

        # only empty strings return false when converted to boolean
        if isVisible == str("false"):
            isVisible = ""
        challenge.isVisible = bool(isVisible)

        # only empty strings return false when converted to boolean
        if isRandomized == str("false"):
            isRandomized = ""
        challenge.isRandomized = bool(isRandomized)

        if displayCorrectAnswer == str("false"):
            displayCorrectAnswer = ""
        challenge.displayCorrectAnswer = bool(displayCorrectAnswer)

        if displayCorrectAnswerFeedback == str("false"):
            displayCorrectAnswerFeedback = ""
        challenge.displayCorrectAnswerFeedback = bool(
            displayCorrectAnswerFeedback)

        if displayIncorrectAnswerFeedback == str("false"):
            displayIncorrectAnswerFeedback = ""
        challenge.displayIncorrectAnswerFeedback = bool(
            displayIncorrectAnswerFeedback)
        
     
        try:
            challenge.startTimestamp = datetime.strptime(request.POST['startTime'], "%m/%d/%Y %I:%M %p")
            challenge.hasStartTimestamp = True
        except ValueError:
            challenge.hasStartTimestamp = False

        try:
            challenge.endTimestamp = datetime.strptime(request.POST['endTime'], "%m/%d/%Y %I:%M %p") 
            challenge.hasEndTimestamp = True
        except ValueError:
            challenge.hasEndTimestamp = False

        try:
            challenge.dueDate = datetime.strptime(request.POST['dueDate'], "%m/%d/%Y %I:%M %p") 
            challenge.hasDueDate = True
        except ValueError:
            challenge.hasDueDate = False

        # Number of attempts
        if('unlimitedAttempts' in request.POST):
            challenge.numberAttempts = unlimited_constant   # unlimited attempts
        else:
            # empty string and number 0 evaluate to false
            num = request.POST['numberAttempts']
            if not num:
                challenge.numberAttempts = unlimited_constant
            else:
                numberAttempts = int(request.POST.get("numberAttempts", 1))
                challenge.numberAttempts = numberAttempts

        # Time to complete the challenge
        if('unlimitedTime' in request.POST):
            challenge.timeLimit = unlimited_constant   # unlimited time
        else:
            # empty string and number 0 evaluate to false
            time = request.POST['timeLimit']
            if not time:
                challenge.timeLimit = unlimited_constant
            else:
                timeLimit = int(request.POST.get("timeLimit", 45))
                challenge.timeLimit = timeLimit
                
        print("challenge")
        print(challenge)
        challenge.save()  # Save challenge to database

        # check if course was selected
        if not challenge.isTeamChallenge:
            addTopicsToChallenge(
                challenge, request.POST['topics'], unspecified_topic, currentCourse)
        # Processing and saving tags in DB
        saveTags(request.POST['tags'], challenge, ObjectTypes.challenge)

        if challenge.hasDueDate:
            create_due_date_process(request, challenge.challengeID,
                                    challenge.dueDate, request.session['django_timezone'])
        else:
            print("No due date selected")
        if challenge.isTeamChallenge:
            return redirect('/oneUp/instructors/teamChallengesList')
        if isGraded == "":
            return redirect('/oneUp/instructors/warmUpChallengeList')
        else:
            return redirect('/oneUp/instructors/challengesList')

    elif request.method == "GET":
        # In case we specify a different number of blanks
        
        if 'num_answers' in request.GET:
            num_answers = request.GET['num_answers']

        if 'warmUp' in request.GET:
            context_dict['warmUp'] = 1
      
        # If challengeID is specified then we load for editing.
        if 'challengeID' in request.GET:
            challenge = Challenges.objects.get(
                pk=int(request.GET['challengeID']))

            # Copy all of the attribute values into the context_dict to
            # display them on the page.
            challengeId = request.GET['challengeID']
            context_dict['challengeID'] = request.GET['challengeID']

            context_dict['challengeDifficulty'] = challenge.challengeDifficulty
            context_dict['curve'] = challenge.curve
          
            for attr in string_attributes:
                data = getattr(challenge, attr)
                if data == unlimited_constant:
                    context_dict[attr] = ""
                else:
                    context_dict[attr] = data

            if challenge.numberAttempts == unlimited_constant:
                context_dict['unlimitedAttempts'] = True
            else:
                context_dict['unlimitedAttempts'] = False

            if challenge.timeLimit == unlimited_constant:
                context_dict['unlimitedTime'] = True
            else:
                context_dict['unlimitedTime'] = False
                
        
            if challenge.hasStartTimestamp:
                context_dict['startTimestamp'] = datetime_to_selected(challenge.startTimestamp)
            else:
                context_dict['startTimestamp'] = ""
            
            if challenge.hasEndTimestamp:
                context_dict['endTimestamp'] = datetime_to_selected(challenge.endTimestamp)
            else:
                context_dict['endTimestamp'] = ""
            
            if challenge.hasDueDate:
                context_dict['dueDate'] = datetime_to_selected(challenge.dueDate)
            else:
                context_dict['dueDate'] = ""

            if challenge.isGraded:
                context_dict['isGraded'] = True
            else:
                context_dict['isGraded'] = False

            if challenge.isVisible:
                context_dict['isVisible'] = True
            else:
                context_dict['isVisible'] = False

            if challenge.isRandomized:
                context_dict['randomizeProblems'] = True
            else:
                context_dict['randomizeProblems'] = False

            if challenge.displayCorrectAnswer:
                context_dict['displayCorrectAnswer'] = True
            else:
                context_dict['displayCorrectAnswer'] = False

            if challenge.displayCorrectAnswerFeedback:
                context_dict['displayCorrectAnswerFeedback'] = True
            else:
                context_dict['displayCorrectAnswerFeedback'] = False

            if challenge.displayIncorrectAnswerFeedback:
                context_dict['displayIncorrectAnswerFeedback'] = True
            else:
                context_dict['displayIncorrectAnswerFeedback'] = False

            # Get the challenge question information and put it in the context
            challenge_questions = ChallengesQuestions.objects.filter(
                challengeID=challengeId).order_by('questionPosition')

            for challenge_question in challenge_questions:
                questionObjects.append(challenge_question)

            # Getting all the questions of the challenge except the matching question
            challengeDetails = Challenges.objects.filter(
                challengeID=challengeId)

            # Extract the topics
            context_dict['topics'] = getTopicsForChallenge(challenge)
            # Extract the tags from DB
            context_dict['tags'] = extractTags(challenge, "challenge")

            #context_dict['all_Topics'] = utils.extractTopics(challenge, "challenge")
#             allTopics = utils.getTopicsForChallenge(challenge)
#             topicNames = ""
#
#             for t in allTopics:
#                 topicNames += t['name'] +"\t\t"
#
#             context_dict['topics_str'] = topicNames
#             context_dict['all_Topics'] = utils.getTopicsForChallenge(challenge)

            context_dict['questionTypes'] = QuestionTypes

            # The following information is needed for the challenge 'view' option
            i = 0
            for q in questionObjects:
                i += 1
                qdict = questionTypeFunctions[q.questionID.type]['makeqdict'](
                    q.questionID, i, challengeId, q, None)
                pp.pprint(qdict)
                qdict = questionTypeFunctions[q.questionID.type]['correctAnswers'](
                    qdict)
                qdict = questionTypeFunctions[q.questionID.type]['modifyQdictForView'](
                    qdict)

                qlist.append(qdict)
        else:
            context_dict['topics'] = []
            context_dict['tags'] = []
            context_dict['isVisible'] = True
            context_dict['displayCorrectAnswer'] = True
            context_dict['manuallyGradedScore'] = '0'
            context_dict['curve'] = '0'

        
            
            ccp = CourseConfigParams.objects.get(courseID=currentCourse)
       #     if ccp.hasCourseStartDate and ccp.courseStartDate <= current_localtime().date():
       #        context_dict['startTimestamp'] = datetime_to_selected(ccp.courseStartDate) 
       #     if ccp.hasCourseEndDate and ccp.courseEndDate > current_localtime().date(): 
       #         context_dict['endTimestamp'] = datetime_to_selected(ccp.courseEndDate) 
       #         context_dict['dueDate'] = datetime_to_selected(ccp.courseEndDate)
                
        context_dict['question_range'] = zip(
            range(1, len(questionObjects)+1), qlist)
        # logger.debug("[GET] " + str(context_dict))

    if 'wView' in request.GET:
        context_dict['warmUp'] = 1
        view = 1
    elif 'view' in request.GET:
        view = 1
    else:
        view = 0
    context_dict['view'] = view == 1
    context_dict['ckeditor'] = config_ck_editor()
    
    # edit
    return render(request, 'Instructors/ChallengeCreateForm.html', context_dict)
