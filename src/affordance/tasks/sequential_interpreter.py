import json
import pdb
import re
import subprocess
import sys
import time
from enum import auto
from re import L
from turtle import pd

import datasets
import numpy as np
import openai
import parsimonious
import requests
from prompt_library import (llm_similar_tasks, random_tasks,
                            similar_auto_breakdowns, similar_tasks)
from tqdm import tqdm
from utils import (Command, OpenAIModel, StacktraceItem, cache_dir, chunks,
                   get_subset, gpt3, parse_program, propose_decomposition,
                   propose_instruction, substring_match)
from pot_tools import safe_execute, simplify_ans

subscription_key = '28dc412935f24fb9974d80c30915483a'
search_url = "https://api.bing.microsoft.com/v7.0/search"
headers = {"Ocp-Apim-Subscription-Key": subscription_key}
from serpapi import GoogleSearch
import time

serp_api_key = "5599fad2263e4b1c6d4efb62fa15f83f5ad2d1bb727b8721d616d719399a7f7b"


import re

# as per recommendation from @freylis, compile once only
CLEANR = re.compile('<.*?>') 

def cleanhtml(raw_html):
  cleantext = re.sub(CLEANR, '', raw_html)
  return cleantext

def search(query, top_k=1):
    params = {"q": query, "textDecorations": True, "textFormat": "HTML"}
    response = requests.get(search_url, headers=headers, params=params)
    response.raise_for_status()
    search_results = response.json()
    if "webPages" in search_results:
        snippets = [cleanhtml(v['name'] + " " + v['snippet']) for v in search_results['webPages']["value"]][:top_k]
    else:
        return "", None
    return "\n".join(snippets), None

def google_search(query, previous_input=None, top_k=1):
    params = {
      "q": query,
    #   "location": "Austin, Texas, United States",
      "hl": "en",
      "gl": "us",
      "google_domain": "google.com",
      "api_key": serp_api_key
    }

    search = GoogleSearch(params)
    res = search.get_dict()

    if 'answer_box' in res.keys() and 'answer' in res['answer_box'].keys():
        toret = res['answer_box']['answer']
    elif 'answer_box' in res.keys() and 'snippet' in res['answer_box'].keys():
        toret = res['answer_box']['snippet']
    elif 'answer_box' in res.keys() and 'snippet_highlighted_words' in res['answer_box'].keys():
        toret = res['answer_box']["snippet_highlighted_words"][0]
    elif 'snippet' in res["organic_results"][0].keys():
        toret= res["organic_results"][0]['snippet'] 
    else:
        toret = None

    return toret, None

def code_generate(instruction, code_input):
    # response = openai.Edit.create(
    # model="code-davinci-edit-001",
    # input="x = " + code_input,
    # instruction="Python code for " + instruction,
    # temperature=0,
    # top_p=1
    # )
    if instruction in code_input:
        # Code input is sufficient to write code
        comment = "\"\"\"\n{0}\nStore the final result as a variable named 'ans' and print it.\n\"\"\"\n\ndef".format(code_input)
    else:
        if re.search("#[0-9]+", instruction):
            input_string = re.search("#[0-9]+", instruction).group(0)
            instruction = instruction.replace(input_string, "Input")
        comment = "\"\"\"\n{0}\nInput:{1}\nStore the final result as a variable named 'ans' and print it.\n\"\"\"\n\ndef".format(instruction, code_input)
        
    response = openai.Completion.create(
    model="code-davinci-002",
    prompt=comment,
    temperature=0.0,
    max_tokens=256,
    top_p=1,
    frequency_penalty=0,
    presence_penalty=0,
    stop=["print(ans)"]
    )
    code_snippet = response.choices[0].text
    return "def" + code_snippet, comment

def code_execute(code_snippet, code_input=None):
    # generally includes a first line consisting of command instruction
    if code_input:
        try:
            code_output = safe_execute(code_input)
            result = simplify_ans(code_output)
            if result:
                return result, code_input
        except:
            pass

    try:
        result = subprocess.run([sys.executable, "-c", code_snippet], capture_output=True, text=True)
        if result.stderr == "":
            return result.stdout, code_snippet
    except:
        pass

    try:
        # Command instruction (if its code) starts in the previous line.
        code_snippet = code_snippet.split("\n", 1)[1]
    except:
        return None, code_snippet
    result = subprocess.run([sys.executable, "-c", code_snippet], capture_output=True, text=True)
    if result.stderr == "":
        return result.stdout, code_snippet

    return None, code_snippet

def code_generate_then_execute(instruction, code_input):
    # output = openai.Edit.create(
    # model="code-davinci-edit-001",
    # input="x = " + code_input,
    # instruction="Python code for " + instruction,
    # temperature=0,
    # top_p=1
    # )
    # code_snippet = output.choices[0].text
    pdb.set_trace()
    code_snippet, generate_details = code_generate(instruction=instruction, code_input=code_input)
    # result = subprocess.run([sys.executable, "-c", code_snippet], capture_output=True, text=True)
    # if result.stderr == "":
    #     return result.stdout, code_snippet
    result = safe_execute(code_snippet)
    result = simplify_ans(result)
    return result, code_snippet


def arithmetic(equations, previous_input):
    # collect all outputs of arithmetic and run python to execute them
    result = subprocess.run([sys.executable, "-c", "\n".join(equations)], capture_output=True, text=True)
    return result, None

def generate(prompt, previous_input):
    return gpt3(prompt)[0], None


class Interpreter():
    def __init__():
        pass


class TopDownVisitor(Interpreter):
    def __init__(self, model_name="text-davinci-002"):
        self.built_ins = {
            "[generate]": generate,
            "[search]": search,
            "[code generate]": code_generate,
            "[code execute]": code_execute,
            "[string edit]" : code_generate_then_execute,
            "[arithmetic]" : arithmetic,
            "[generate python code]" : code_generate,
        }

        self.program_completer = OpenAIModel(model=model_name,  max_length=500, quote='', n=1)

    def syntax_check(self, program):
        # Look for programs with EOC ending and final answer in syntax.
        return "[EOQ]\nAns:" in program

    def batch_visit(self, prefixes, programs):
        answers = []
        for prefix, program in tqdm(zip(prefixes, programs)):
            answers.append(self.visit(prefix, program))
        return answers

    def check_builtin(self, command):
        # Some multiple commands are mapped to the same built-in affordances.
        processed_command = command
        if command in self.built_ins:
            return self.built_ins[processed_command] 
        # What to do when no built-infunction is triggered.

    def complete_program(self, prefix, program):
        # Check for out-of-window errors and shift window to reduce the number of programs shown.
        continuation = program
        runs = 5
        while not self.syntax_check(continuation) and runs > 0:
            continuation = self.program_completer(prefix + program)[0]
            runs -= 1
        return program + continuation

    def rerun_program(self, prefix, prev_command_list, current_command_output):
        # Check for out-of-window errors and shift window to reduce the number of programs shown.
        program_prefix = prefix + "\n".join([Command.convert_to_nlprogram(i+1, command) for i, command in enumerate(prev_command_list[:-1])])
        # TODO: Add feature for multiline
        program_prefix += "\n" + Command.convert_to_nlprogram(len(prev_command_list), prev_command_list[-1], input_only=True) + "\n#{0}: {1}".format(len(prev_command_list), current_command_output)
        # Replace last line with corrected input, if necessary.
        continuation = self.program_completer(program_prefix)[0]
        return program_prefix, continuation

    def visit(self, prefix, program):
        
        
        program_ends = self.syntax_check(program)
        program = prefix.rsplit("----\n", 1)[1].split("\n", 1)[1] + program
        # If program does not end correctly, rerun GPT-3 further to end it. 
        if program_ends:
            parsed_program =  parse_program(program)
        else:
            program = self.complete_program(prefix, program)
            program_ends = self.syntax_check(program)
            if not program_ends:
                # After one attempt at completing program, give up checking for affordance
                return program
            parsed_program =  parse_program(program)

        previous_input = None
        i = 0
        input_node = parsed_program.input_node
        command_node_list = parsed_program.node_list
        answer_node = parsed_program.answer_node
        stack_trace = []
        while command_node_list[i].command_name != "[EOQ]":
            
            node = command_node_list[i]
            command_name = node.command_name
            command_input = node.command_input
            command_output = node.command_output
            # Check affordance list to run.
            affordance = self.check_builtin(command=command_name)
            
            if affordance and previous_input: # previous_input so code generate is hard, But search and code_execute is not, overrride.
                affordance_output, affordance_details = affordance(command_input, previous_input)
                if affordance_output:
                    # affordance_output and command_output (i.e. GPT-3) need to be interpolated appropriately.
                    # For code executions
                    command_output = affordance_output
                    # For Search (concatenate the GPT_3 output with retrieval output)
                
                # Run GPT-3 again.
                program_prefix, rerun_program = self.rerun_program(prefix, command_node_list[:i+1], command_output)

                
                # Replace nodes i+1 though N (again complication is that this should be done EOC i.e. till the program is fully generated.)
                new_program = program_prefix + rerun_program
                new_program = new_program.rsplit("----\n", 1)[1].split("\n", 1)[1]
                try:
                    parsed_rerun_program = parse_program(new_program)
                except:
                    pdb.set_trace()

                parsed_program = parsed_rerun_program
                program = new_program


                # for j in range(i+1, len(command_node_list)):
                #     command_node_list[j] = parsed_rerun_program.node_list[j]
                command_node_list = command_node_list[:i+1] + parsed_rerun_program.node_list[i+1:]

                answer_node = parsed_rerun_program.answer_node

                stack_trace.append(StacktraceItem(node, affordance, affordance_output, affordance_details, new_program, parsed_rerun_program))
            
            # Output of the current command becomes the input for the next round.
            previous_input = command_output
            i += 1

        # Since run is complete, The final answer in parsed_rerun_program is returned
        # Selectively, we can also return the entire program
        return program


class TopDownVisitorBeta(Interpreter):
    def __init__(self, model_name="text-davinci-002"):
        self.built_ins = {
            "[generate]": generate,
            "[search]": google_search,
            "[code generate]": code_generate,
            "[code execute]": code_execute,
            "[string edit]" : code_generate_then_execute,
            "[arithmetic]" : arithmetic,
            "[generate python code]" : code_generate,
        }

        self.program_completer = OpenAIModel(model=model_name,  max_length=500, quote='', n=1)

    def syntax_check(self, program):
        # Look for programs with EOC ending and final answer in syntax.
        return "[EOQ]\nAns:" in program

    def batch_visit(self, prefixes, programs):
        answers = []
        for prefix, program in tqdm(zip(prefixes, programs)):
            answers.append(self.visit(prefix, program))
        return answers

    def check_builtin(self, command):
        # Some multiple commands are mapped to the same built-in affordances.
        processed_command = command
        if command in self.built_ins:
            return self.built_ins[processed_command] 
        # What to do when no built-infunction is triggered.

    def shorten_prefix(self, prefix, to_remove=1):
        # Program prefix contains other demonstrations, along with Q1:
        individual_programs = prefix.split("\n----\n")
        
        # to Remove: Number of programs to remove 
        # If they are organized by task relevance probably best to get rif of the latter ones.
        shifted_programs = individual_programs[:-(to_remove+1)] + [individual_programs[-1]]
        new_prefix = "\n----\n".join(shifted_programs)
        
        return new_prefix

    def complete_program(self, prefix, program):
        # Check for out-of-window errors and shift window to reduce the number of programs shown.
        continuation = program
        runs = 5
        prefix = self.shorten_prefix(prefix, 1)
        while not self.syntax_check(continuation) and runs > 0:
            continuation = self.program_completer(prefix + continuation)[0]
            continuation = program + continuation
            runs -= 1
        return continuation

    def rerun_program(self, prefix, prev_command_list, current_command_output):
        # Check for out-of-window errors and shift window to reduce the number of programs shown.
        program_prefix = prefix + "\n".join([Command.convert_to_nlprogram(i+1, command) for i, command in enumerate(prev_command_list[:-1])])
        # TODO: Add feature for multiline
        program_prefix += "\n" + Command.convert_to_nlprogram(len(prev_command_list), prev_command_list[-1], input_only=True) + "\n#{0}: {1}\nQ".format(len(prev_command_list), current_command_output.strip())
        # Replace last line with corrected input, if necessary.
        continuation = self.program_completer(program_prefix)[0]
        return program_prefix, continuation

    def visit(self, prefix, program):
        
        
        program_ends = self.syntax_check(program)
        program = prefix.rsplit("----\n", 1)[1].split("\n", 1)[1] + program
        # If program does not end correctly, rerun GPT-3 further to end it. 
        if program_ends:
            try:
                parsed_program =  parse_program(program)
            except:
                # If the initial program is not parsed even if it has an ending, return as is
                return program
        else:
            new_program = self.complete_program(prefix, program)
            program_ends = self.syntax_check(new_program)
            if not program_ends:
                # After one attempt at completing program, give up checking for affordance
                return program
            try:
                parsed_program =  parse_program(new_program)
            except:
                return program

        previous_input = None
        i = 0
        input_node = parsed_program.input_node
        command_node_list = parsed_program.node_list
        answer_node = parsed_program.answer_node
        stack_trace = []

        if not program_ends:
            # After one attempt at completing program, give up checking for affordance
            return program

        previous_input = input_node.text

        while  command_node_list[i].command_name != "[EOQ]":

            node = command_node_list[i]
            command_name = node.command_name
            command_input = node.command_input
            command_output = node.command_output
            # Check affordance list to run.
            affordance = self.check_builtin(command=command_name)


            # if affordance and previous_input:
            if affordance:
                
                affordance_output, affordance_details = affordance(command_input, previous_input)
                if affordance_output:
                    # affordance_output and command_output (i.e. GPT-3) need to be interpolated appropriately.
                    # For code executions
                    command_output = affordance_output
                    # For Search (concatenate the GPT_3 output with retrieval output)

                # Run GPT-3 again (technically only if the execution is different)
                program_prefix, rerun_program = self.rerun_program(prefix, command_node_list[:i+1], command_output)
                
                # Replace nodes i+1 though N (again complication is that this should be done EOC i.e. till the program is fully generated.)
                new_program = program_prefix + rerun_program
                new_program = new_program.rsplit("----\n", 1)[1].split("\n", 1)[1]
                new_program = self.complete_program(prefix, new_program)
                
                try:
                    parsed_rerun_program = parse_program(new_program)
                except:
                    # Everytime the rerun fails to generate a legitimate program, its unsafe to assume that it could replace the rest of the original program. 
                    # Best course of action is to exit with the best new_program
                    program = new_program
                    break

                parsed_program = parsed_rerun_program
                program = new_program
                command_node_list = command_node_list[:i+1] + parsed_rerun_program.node_list[i+1:]
                answer_node = parsed_rerun_program.answer_node

                stack_trace.append(StacktraceItem(node, affordance, affordance_output, affordance_details, new_program, parsed_rerun_program))
            
            # Output of the current command becomes the input for the next round.
            previous_input = command_output
            i += 1
        # Since run is complete, The final answer in parsed_rerun_program is returned
        # Selectively, we can also return the entire program
        return program



