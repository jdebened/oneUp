#
# Created  updated 04/06/2020
# GGM
#

import random
import re
from decimal import Decimal

from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render
from django.template.defaultfilters import length
from django.templatetags.i18n import language
from sqlparse.utils import indent

from Badges.enums import ObjectTypes
from Instructors.constants import (unassigned_problems_challenge_name,
                                   unlimited_constant)
from Instructors.models import (Answers, Challenges, ChallengesQuestions,
                                CorrectAnswers, StaticQuestions)
from Instructors.questionTypes import QuestionTypes
from Instructors.views.utils import (addSkillsToQuestion, extractTags,
                                     getCourseSkills, getSkillsForQuestion,
                                     initialContextDict, saveTags,
                                     update_or_create_challenge_questions)
from oneUp.ckeditorUtil import config_ck_editor
from oneUp.decorators import instructorsCheck
from oneUp.logger import logger


##this file makes no sense without the attached questionTypes.py
##questionTypes.py calls all the functions here in order.
##look there to find out what to change here or you will make a mess.
@login_required
@user_passes_test(instructorsCheck,login_url='/oneUp/students/StudentHome',redirect_field_name='')
def parsonsForm(request):
    context_dict, currentCourse = initialContextDict(request)

    # In this class, these are the names of the attributes which are strings.
    # We put them in an array so that we can copy them from one item to
    # another programmatically instead of listing them out.
    string_attributes = ['preview','questionText','difficulty','correctAnswerFeedback',
                  'incorrectAnswerFeedback','instructorNotes','author'];

    if 'view' in request.GET:
        context_dict['view'] = 1
             
    context_dict['skills'] = getCourseSkills(currentCourse)
    context_dict['tags'] = []
    if request.method == 'POST':

        # If there's an existing question, we wish to edit it.  If new question,
        # create a new Question object.
        if 'questionId' in request.POST:
            qi = request.POST['questionId']
            if not qi == "":
                logger.debug('questionId '+request.POST['questionId'])
                question = StaticQuestions.objects.get(pk=int(qi))
            else:
                question = StaticQuestions()
        else:
            question = StaticQuestions()

        # Copy all strings from POST to database object.
        for attr in string_attributes:
            setattr(question,attr,request.POST[attr])
            
        question.difficulty = request.POST['difficulty']
        
        # Fix the question type
        question.type = QuestionTypes.parsons
        
        if question.author == '':
            question.author = request.user.username
        
        if 'strongHint' in request.POST:
            question.strongHint = request.POST['strongHint']
        if 'basicHint' in request.POST:
            question.basicHint = request.POST['basicHint']
        question.isHintUsed = "hintUsed" in request.POST   
        question.save();  
        
        # Save the entered model solution as "correctAnswer"        
        answers = Answers.objects.filter(questionID=question)
        if answers:
            answer = answers[0]
            answer.answerText = "";
            languageName = request.POST['languageName']
            indentationBoolean = request.POST['indetationBoolean']
            
            languageName = "Language:"+languageName+";"
            indentationBoolean = "Indentation:" + indentationBoolean+";"
            
            instructorLine = languageName + indentationBoolean
            answer.answerText += instructorLine
            setUpCode = request.POST['setupCode']
            #print("setupcode", setUpCode)
            setUpCode = re.sub("\r\n\r\n", "\r\n", setUpCode)
            answer.answerText += setUpCode.strip()
            #print("Answer edit answer:", repr(answer.answerText))
            answer.save()
            # no need to change correct answer
            #correctAnswerObject = CorrectAnswers.objects.filter(questionID=question)
        else:
            answer = Answers()         
            answer.questionID = question
           #we are crafting a new answer in this section
           # answer.answerText = request.POST['setupCode']
            
            answer.answerText = ""
            languageName = request.POST['languageName']
            indentationBoolean = request.POST['indetationBoolean']
            
            languageName = "Language:"+languageName+";"
            indentationBoolean = "Indentation:" + indentationBoolean+";"
            
            instructorLine = languageName + indentationBoolean
            
            
            answer.answerText += instructorLine
            setUpCode = request.POST['setupCode']
            #print("setupcode", setUpCode)
            ##setUpCode = re.sub("\r\n\s{4}", "\t", setUpCode)
            answer.answerText += setUpCode
            #print("Answer new answer", answer.answerText)
            answer.save()
            # the answer is also the correct answer - model solution to be displayed to the student
            correctAnswerObject = CorrectAnswers()
            correctAnswerObject.questionID = question           
            correctAnswerObject.answerID = answer
            correctAnswerObject.save()

       
        # Processing and saving tags in DB
        saveTags(request.POST['tags'], question, ObjectTypes.question)
        positions = []
        if 'challengeID' in request.POST:
            # save in ChallengesQuestions if not already saved            
            update_or_create_challenge_questions(request,question)

            # Processing and saving skills for the question in DB
            addSkillsToQuestion(currentCourse,question,request.POST.getlist('skills[]'),request.POST.getlist('skillPoints[]'))
                    
                
            redirectVar = redirect('/oneUp/instructors/challengeQuestionsList', context_dict)
            redirectVar['Location']+= '?challengeID='+request.POST['challengeID']
            return redirectVar
        # Question is unassigned so create unassigned challenge object
        challenge = Challenges()
        challenge.challengeName = unassigned_problems_challenge_name
        challenge.courseID = currentCourse
        challenge.numberAttempts = unlimited_constant
        challenge.timeLimit = unlimited_constant
        challenge.save()
        ChallengesQuestions.addQuestionToChallenge(question, challenge, 0, 0)
        
        redirectVar = redirect('/oneUp/instructors/challengeQuestionsList?problems', context_dict)
        return redirectVar

    elif request.method == 'GET':
            
        if 'challengeQuestionID' in request.GET:
            context_dict['challengeQuestionID'] = request.GET['challengeQuestionID']

        if 'challengeID' in request.GET:
            context_dict['challengeID'] = request.GET['challengeID']
            chall = Challenges.objects.get(pk=int(request.GET['challengeID']))
            context_dict['challengeName'] = chall.challengeName
            context_dict['challenge'] = True
            context_dict['tags'] = []
            
            if Challenges.objects.filter(challengeID = request.GET['challengeID'],challengeName=unassigned_problems_challenge_name):
                context_dict["unassign"]= 1
            
        # If questionId is specified then we load for editing.
        if 'questionId' in request.GET:
            question = StaticQuestions.objects.get(pk=int(request.GET['questionId']))
            
            # Copy all of the attribute values into the context_dict to display them on the page.
            context_dict['questionId']=request.GET['questionId']
            
            for attr in string_attributes:
                context_dict[attr]=getattr(question,attr)

            # Load the model solution, which is stored in Answers
            answer = Answers.objects.filter(questionID=question)
            answer = answer[0].answerText
            context_dict['languageName'] = re.search(r'Language:([^;]+)', answer).group(1).lower().lstrip()
            context_dict['indentation'] = re.search(r';Indentation:([^;]+);', answer).group(1)
            #print("language", context_dict['languageName'])
            #print("indentation", context_dict['indentation'])
            
            languageAndLanguageName = re.search(r'Language:([^;]+)', answer)
            intentationEnabledVariableAndValue = re.search(r';Indentation:([^;]+);', answer)
            answer = answer.replace(languageAndLanguageName.group(0), "")
            answer = answer.replace(intentationEnabledVariableAndValue.group(0), "")
            
            answer =  re.sub("^ *\\t", "  ", answer)
            
            #tokenizer characters ☃ and ¬
            answer = re.sub("\n", "\n¬☃", answer)
            answer = re.sub("^[ ]+?", "☃", answer)
            
            #we turn the student solution into a list
            answer = [x.strip() for x in answer.split('¬')]
            
            #get how many spces there are in the first line
            #print("answer[0]",answer[0])
            answer[0] = re.sub("☃"," ",answer[0])
            leadingSpacesCount = len(answer[0]) - len(answer[0].lstrip(' '))
            #print("leading spaces", leadingSpacesCount)
            
            #give each string the new line
            tabedanswer = []
            lengthOfModelSolution = len(answer)
            for index, line in enumerate(answer):
                line = re.sub("☃", "", line)
                line = re.sub("^[ ]{" + str(leadingSpacesCount) + "}", "", line)
                if index < len(answer)- 1:
                    line = line +"\n"
                tabedanswer.append(line)
            
            answer = ""
            answer = answer.join(tabedanswer)
            
            
            context_dict['model_solution'] = answer
            
 
            # Extract the tags from DB            
            context_dict['tags'] = extractTags(question, "question")
            
            if 'challengeID' in request.GET:
                # get the challenge points for this problem to display
                if 'challengeQuestionID' in request.GET:
                    challenge_questions = ChallengesQuestions.objects.filter(pk=int(request.GET['challengeQuestionID']))
                    if challenge_questions:
                        context_dict['points'] = challenge_questions[0].points
                        
                        # set default skill points - 1
                        context_dict['q_skill_points'] = int('1')
        
                        # Extract the skill                                        
                        context_dict['selectedSkills'] = getSkillsForQuestion(currentCourse,question)   
                else:
                    context_dict['points'] = 0
                context_dict['basicHint'] = question.basicHint
                context_dict['strongHint'] = question.strongHint
                context_dict['hintUsed'] = question.isHintUsed
                

            #print("loaded feedback")
            context_dict['incorrectAnswerFeedback'] = question.correctAnswerFeedback
            context_dict['incorrectAnswerFeedback'] = question.incorrectAnswerFeedback

        if 'questionId' in request.POST:         
            return redirect('challengesView')
        
        context_dict['ckeditor'] = config_ck_editor()
    
    return render(request,'Instructors/ParsonsForm.html', context_dict)
#parsons2 functions after here
#parsons grading software for grading inside the system.
@login_required
def parsonDynamicGrader(request):
    ##this is used to track how many times the student clicks class average
    ##we use ajax to track the information, otherwise they'd get the page refreshed on them
    ##and it would be "wrong".
    from django.http import JsonResponse

    context_dictionary,current_course = studentInitialContextDict(request)
    student_id = context_dictionary['student']
    ##if we posted data with ajax, use it, otherwise just return.
    if request.POST:
        #gradeParson()
        #print("ajax call", context_dictionary, student_id)
        return JsonResponse({})


#these functions contain everything to Parson2, debug here
def findLanguage(solution_string):
    return re.search(
    r'Language:([^;]+)', solution_string).group(1).lower().lstrip()
     
def findIndentation(solution_string):
    return re.search(r';Indentation:([^;]+);',
                                     solution_string).group(1)

def findAnswerText(solution_string):
    language_removed = re.search(r'Language:([^;]+)', solution_string)
    solution_string = solution_string.replace(language_removed.group(0), "")
    #print("language_removed", solution_string)

    indentation_removed = re.search(r';Indentation:([^;]+);',solution_string)
    solution_string = solution_string.replace(indentation_removed.group(0), "")
    #print("indentation_removed", solution_string)
    return solution_string

def findDistractorLimit(solution_string, question):
    distractor_limit = len(
                            re.findall(r'(?=#dist)',
                            repr(solution_string).strip('"\''))
                        )

    if (question.difficulty == "Easy"):
        distractor_limit = 0
    if (question.difficulty == "Medium"):
        distractor_limit = int(distractor_limit / 2)

    #print("distractor limit", distractor_limit)
    return distractor_limit

#for the indentation we should take 4 spaces to equal one Tab character known as \t
def getIndentation(line):
    line = snowmanRemoval(line)
    #print("line indentation regular", repr(line), len(line) - len(line.lstrip(' ')))
    return len(line) - len(line.lstrip(' '))

def getIndentationHash(line, solution_string):
    next_element = solution_string[solution_string.index(line) + 1]

    line = snowmanRemoval(line)
    next_element =  snowmanRemoval(next_element)

    current_line_spacing = len(line) - len(line.lstrip(' '))
    next_line_spacing = len(next_element) - len(next_element.lstrip(' '))

    difference = current_line_spacing - next_line_spacing

    #print("line indentation hash", repr(line), difference, current_line_spacing)
    if(difference == 4):
        return next_line_spacing
        if(current_line_spacing == 8):
            return 8
    if(difference == -4 or difference == 0):
        return current_line_spacing

def generateIndenation(solution_string):
    indentation = []
    hash_pattern = re.compile("##")
    distractor_pattern = re.compile("#dist")
    skip = False
    for line in solution_string:
        if(skip):
            skip = False
            continue
        #if its not a distractor line, then determine if its a hash pattern or not
        if(not distractor_pattern.search(line)):
            if(hash_pattern.search(line)):
                indentation.append(getIndentationHash(line, solution_string))
                skip = True
            else:
                indentation.append(getIndentation(line))
    
    #print("indentaiton array", indentation)
    return indentation

#this model solution is used as the problem displayed to student
#it is called model solution due to historical reasons
#historical reasons like parsons1 naming the problem model solution
#the way this works is that we first generate the Indentation off the pristine copy
#then the pristine copy is modified for display
#and then the student solves it in the UI to have their solution match the pristine copy
def getModelSolution(solution_string, distractor_limit):
    formattedCode = {}
    model_solution = []
    display_code = {}
    distractors = []
    indentation = generateIndenation(solution_string)
    skip_flag = 0
    hash_pattern = re.compile("##")
    distractor_pattern = re.compile("#dist")

    for line in solution_string:
        #print("currentLine", repr(line))
        if(distractor_pattern.search(line)):
            distractors.append(line)
            continue
        if(skip_flag == 1):
            #if indentation flag is 1 then we know that next line line we must skip
            skip_flag = 0
            continue
        if (hash_pattern.search(line)):
            #print("hash pattern found for line", line)
            #get the next element to determine how indented it is
            next_element = solution_string[solution_string.index(line) + 1]
            
            line = re.sub("☃", "", line)
            next_element = re.sub("☃", "", next_element)
            next_element = re.sub("##", "", next_element)
            #we use the difference to calculate whether we must indent and where
            leading_space_count_current_line = len(line) - len(line.lstrip(' '))
            leading_space_count_next_line = len(next_element) - len(next_element.lstrip(' '))

            #this difference will allow us to know how indented they are
            difference = leading_space_count_current_line - leading_space_count_next_line
            #print("linediff", difference)
            skip_flag = 1

            if (difference == -4):
                ##print("-4diff")
                #if indentation is after the line
                #example:
                #data++;
                #   index++;
                next_element = re.sub("^", "\n&nbsp;", next_element)
                line += next_element
            if (difference == 4):
                #print("4diff", repr(line))
                #print("next element", repr(next_element))
                next_element = re.sub("^\s{8}", "", next_element)
                next_element = re.sub("^\s{4}", "", next_element)
                #print("next element", repr(next_element))
                #if indentation is before the line
                #example:
                #   data++;
                #index++;
                #print("line before", repr(line))
                line = re.sub("\s{12}(?=.*; ##)", "    ", line)
                line = re.sub("\s{8}(?=.*; ##)", "    ", line)
                line = re.sub("$", "\n", line)
                #print("line after", repr(line))
                line += next_element
            if(difference == 0):
                #print("zero diff\n")
                line = re.sub("^ *", "", line)
                next_element = re.sub("^ *", "\n", next_element)
                line += next_element
            #print("added line\n", line)
        else:
            line = re.sub("☃ *", "", line)
        line = re.sub("##", "", line)
        if(line != ''):
            model_solution.append({'line':line, 'hashVal':str(hash(line))})
            display_code.update({str(hash(line)): re.sub("&nbsp;", "", line)})

    distractor_counter = 0
    for distractor in distractors:
        if(distractor_counter < distractor_limit):
            #print("distractor", distractor)
            distractor = cleanseDistractor(distractor)
            model_solution.append({'line':distractor, 'hashVal':str(hash(distractor))})
            display_code.update({str(hash(distractor)): re.sub("&nbsp;", "", distractor)})
        distractor_counter += 1

    #print("model solution", model_solution)
    #print("display_code", display_code)
    #print("indentation", indentation)
    random.shuffle(model_solution)
    formattedCode['model_solution'] = model_solution
    formattedCode['display_code'] = display_code
    formattedCode['indentation'] = indentation
    return formattedCode

#generate student solution from the post in student challenges
def generateStudentSolution(student_solution_JSON, student_trash_JSON, line_dictionary):
    student_solution_dict = {}
    student_solution_string = []
    student_solution = []
    student_hashes = []
    student_indentation = []
    student_trash = []

    #print("student_solution_JSON, student_trash_JSON", student_solution_JSON, student_trash_JSON)
    for code_fragment in student_solution_JSON:
        hash_value = str(code_fragment['id'])
        if hash_value != 'None':
            student_hashes.append(hash_value)
            student_indentation.append(0)
            student_solution_string.append(str(line_dictionary[hash_value]) +"\n")
            student_solution.append(line_dictionary[hash_value])


        if 'children' in code_fragment:
            childFragmentFunction(code_fragment, 4, line_dictionary, student_hashes, student_indentation, student_solution_string, student_solution)


    for code_fragment in student_trash_JSON:
        hash_value = code_fragment['id']
        if hash_value != 'None':
            student_trash.append(str(line_dictionary[hash_value]))

    student_solution_string = "".join(student_solution_string)
    student_solution_dict['student_solution_string'] = student_solution_string
    student_solution_dict['student_solution'] = student_solution
    student_solution_dict['student_hashes'] = student_hashes
    student_solution_dict['student_trash'] = student_trash
    student_solution_dict['student_indentation'] = student_indentation

    #print("student solution string", student_solution_string)
    #print("student solution", student_solution)
    #print("student hashes ", student_hashes)
    #print("student trashes", student_trash)
    #print("student indentation", student_indentation)

    return student_solution_dict

def getCorrectCount(student_hashes, hash_solutions):
    correct_count = 0
    i = 0
    #******HOW TO******
    #here is where we determine how many lines the student has entered correctly
    #the student lines come in as hashes and are compared to the correct solutions(keys)
    #if it breaks due to undex error we have run out of lines in either student hashes or keys

    #print("length of submitted keys", len(student_hashes))
    #print("student keys submitted", student_hashes)
    #print("lengh of hash solutions", len(hash_solutions))
    #print("hash solutions", hash_solutions)
    for key in hash_solutions.keys():
        try:
            #while the range we are in is lower than student hashes
            #print("student hash", i, key, student_hashes[i])
            if(student_hashes[i] == key):
                correct_count += 1
            i += 1
        except IndexError:
            break    
    #print("correct count", correct_count)   
    return correct_count
def getIndenationErrorCount(student_indentation, indentation_solution):
    errors = []
    

    for i in range(len(indentation_solution)):
        try:
            if(student_indentation[i] != indentation_solution[i]):
                #print("Indentation error student", student_indentation[i], indentation_solution[i])
                errors.append("Indentation line "+ str(i))
        except IndexError:
            True
    #print("indentation error count", student_indentation, indentation_solution, errors)
    return errors
def gradeParson(qdict, studentAnswerDict):
    #print("studentAnswerDict['correct_line_count']", studentAnswerDict['correct_line_count'])
    if studentAnswerDict['student_solution'] == "" or  studentAnswerDict['student_solution'] == None:
        return 0
    if studentAnswerDict['correct_line_count'] == 0:
        return 0
        
    student_grade = 0
    if (studentAnswerDict['indentation_errors'] == None or len(studentAnswerDict['indentation_errors']) == 0):
        student_grade = qdict['total_points']
    max_available_points = qdict['total_points']
    penalties = Decimal(0.0)

    student_solution_line_count = len(studentAnswerDict['student_solution'])
    correct_lines_in_solution = len(qdict['display_code']) - qdict['distractor_limit']

    #print("Student solution line count", student_solution_line_count)
    #print("studentAnswerDict['correct_line_count']", studentAnswerDict['correct_line_count'])
    #print("correct_lines_in_solution", correct_lines_in_solution)

    #student solution line count makes sure that the right number of lines were submitted
    if (student_solution_line_count < correct_lines_in_solution):
        penalties += Decimal((correct_lines_in_solution - student_solution_line_count) * (1 / correct_lines_in_solution))
        #print("Penalties too few!: ", penalties)

    if (student_solution_line_count > correct_lines_in_solution):
        penalties += Decimal((student_solution_line_count - correct_lines_in_solution) * (1 / correct_lines_in_solution))
        #print("Penalties too many!: ", penalties)

    #correct line count contains the number of correct lines that a student submitted
    error_count = (correct_lines_in_solution - studentAnswerDict['correct_line_count'])
    penalties += Decimal(error_count * (1 / correct_lines_in_solution) * (1/2))

    student_solution_line_count 
    if str2bool(qdict['indentation_flag']):
        #if the student has gotten a line wrong they shouldn't be penalized for its indentation
        adjusted_indentation_error_count = len(studentAnswerDict['indentation_errors']) - error_count
        #print("Indentation flag exists", qdict['indentation_flag'])
        if adjusted_indentation_error_count > 0:
            ##we multiply by 1/2 because each wrong is half of 1/n
            #print("length of indentation errors", len(studentAnswerDict['indentation_errors']))
            penalties += Decimal((len(studentAnswerDict['indentation_errors']) / correct_lines_in_solution) * (1/2))
            #print("indentation error penalties", penalties, studentAnswerDict['indentation_errors'],  correct_lines_in_solution)

    if studentAnswerDict['feedback_button_click_count'] > 0:
        max_available_points /= studentAnswerDict['feedback_button_click_count'] * 2
    else:
        max_available_points = qdict['total_points']

    #max points is the maximum points student can earn, and we subtract the penalties
    student_grade = float(max_available_points) - (float(max_available_points) * float(penalties))
    if student_grade < 0:
        student_grade = 0
    #print("student_grade", student_grade)
    #print("max available points", max_available_points)
    #print("penalties", penalties)
    #print("scaled penalties", float(max_available_points) * float(penalties))
    return round(Decimal(student_grade), 2)

#function that cleans the disctractor of the #distractor and of the ☃(snowman) symbol
def cleanseDistractor(distractor):
    distractor = re.sub("^☃", "", distractor)
    distractor = re.sub("#distractor", "", distractor)
    distractor = re.sub("#dist", "", distractor)
    return distractor

def snowmanRemoval(line):
    line = re.sub("☃", "",line)
    return line
def tabsToSpacesConverter(line):
    line = re.sub("^\t\t\t", "                ", line)
    line = re.sub("^\t\t", "        ", line)
    line = re.sub("^\t", "    ", line)
    return line
def convertTabsToSpaces(solution_string):
    converted_solution = []
    for line in solution_string:
        converted_solution.append(tabsToSpacesConverter(line))
    return "".join(converted_solution)

#mystical code that uses refraction to do a deep dive into the child nodes and retreieve them.
def childFragmentFunction(children, level, line_dictionary, student_hashes, student_indentation, student_solution_string, student_solution):
    ending_curly_pattern = re.compile("(}\n|} \n)")
    semicolon_curly_pattern = re.compile("; \n")
    return_pattern = re.compile("(?=return.*;\n)")
    spacer = " " * level
    for child in children['children']:
        hash_value = child['id']
        student_hashes.append(hash_value)
        student_indentation.append(level)
        #print("line before", repr(line_dictionary[hash_value]))
        line = line_dictionary[hash_value]

        is_ending_curly = ending_curly_pattern.search(line)
        is_semicolon_curly_pattern = semicolon_curly_pattern.search(line)
        is_return_pattern = return_pattern.search(line)
        if(is_ending_curly or is_semicolon_curly_pattern):
            line = re.sub("; \n",";\n" + spacer,line)
            line = re.sub("(}\n|} \n)", "}\n" + spacer, line)

        if(is_return_pattern):
            line = re.sub("(?=return.*;\n)", spacer, line)
        #print("line after", repr(line))

        if(not is_ending_curly or not is_semicolon_curly_pattern or not is_return_pattern):
            student_solution_string.append(" " * level + str(line) + "\n")

        student_solution.append(line_dictionary[hash_value])

        if 'children' in child:
            childFragmentFunction(child, level+4, line_dictionary, student_hashes, student_indentation, student_solution_string, student_solution)
    
    #print("student hashes, student indentation, student solution string, student solution", student_hashes, student_indentation, student_solution_string, student_solution)
def str2bool(v):
  return v.lower() in ("yes", "true", "t", "1")    
# def getDisplayForACE():
#     solution_hashes.append(hash(line))
#         line_array.update({hash(line):line})
#         model_solution.append({'line':line, 'hashVal':hash(line)})
