import argparse
from collections import Counter
import json
import re

import datasets
import numpy as np
from prompt_library import llm_similar_tasks, random_tasks, similar_auto_breakdowns, similar_tasks
from sequential_interpreter import TopDownVisitor
from tqdm import tqdm
from transformers import GPT2Tokenizer
from utils import (
    OpenAIModel,
    cache_dir,
    chunks,
    get_answer,
    get_autocot_answer,
    get_few_shot_prompt,
    substring_match,
)

tokenizer = GPT2Tokenizer.from_pretrained("gpt2")

d = datasets.load_dataset("bigbench", "physics_questions", cache_dir=cache_dir)
inputs = d["train"]["inputs"] + d["validation"]["inputs"]
labels = d["train"]["targets"] + d["validation"]["targets"]
labels = [l[0] for l in labels]

train_inputs = d["train"]["inputs"]
train_labels = d["train"]["targets"]

io_pairs = [
    (
        """Q: Lamar Gant, U.S. powerlifting star, became the first man to deadlift five times his own body weight in 1985. Deadlifting involves raising a loaded barbell from the floor to a position above the head with outstretched arms. Determine the work done by Lamar in deadlifting 260 kg to a height of 0.85 m above the ground.""",
        "A:2165.8 J",
    ),
    (
        """Q: In the Funny Car competition at the Joliet Speedway in Joliet, Illinois in October of 2004, John Force complete the 1/4-mile dragster race in a record time of 4.437 seconds. Determine the average speed of the dragster in km/hr.""",
        "A:326.4 km/hr",
    ),
    (
        """Q: A bicycle has a momentum of 24 kg*m/s. What momentum would the bicycle have if it had one-half the mass and was moving with thrice the speed?""",
        "A:72 kg*m/s",
    ),
]

task_description = "Answer these high-school-level physics multiple-choice questions."


def exact_match(labels, predictions):
    correct = 0
    count = 0
    for label, predict in zip(labels, predictions):
        if label.lower() == predict.lower():
            correct += 1
        count += 1
    return (1.0 * correct) / count


def token_match(labels, predictions):
    correct = 0
    count = 0
    for label, predict in zip(labels, predictions):
        if label.lower() in [p.lower() for p in predict]:
            correct += 1
        count += 1
    return (1.0 * correct) / count


def few_shot(N=10, temperature=0.3, model_name="text-davinci-002"):
    few_shot_prompt = get_few_shot_prompt(train_inputs, train_labels, n=N)
    print(len(tokenizer(few_shot_prompt)["input_ids"]))

    def predict(chunk):
        gpt3 = OpenAIModel(
            model=model_name, max_length=200, temperature=temperature, quote="---", n=1
        )
        prompts = ["%s\n%s".format(few_shot_prompt, x) for x in chunk]
        return gpt3(prompts)

    perf_array = []
    runs = 5
    for run in range(runs):
        print(f"run {run+1}/{runs}")
        answers = []
        for x in tqdm(chunks(inputs, 10)):
            answers.extend(predict(x))
        preds = [x.strip() for x in answers]
        perf_array.append(exact_match(labels, preds))
    print("No decomposition Performance:")
    print("Mean", np.mean(perf_array))
    print("Std. Dev", np.std(perf_array))


few_shot_cot_prompt = """In these examples, you are given a task description and an input. Break the input down into subtasks in order to solve the task. You can generate python code to solve arithmetic and algebra equations in using functions from sympy.
from sympy import Symbol
from sympy import simplify
import math
from sympy import solve_it
# solve_it(equations, variable): solving the equations and return the variable value.

Description: Solve the following arithmetic problems on ratios and fractions, writing out intermediate arithmetic calculations as python code. Store your result as a variable named 'ans'.
Input:  In a flight of 600 km, an aircraft was slowed down due to bad weather. Its average speed for the trip was reduced by 200 km/hr and the time of flight increased by 30 minutes. The duration of the flight is:  A)1 hour B)2 hours C)3 hours D)4 hours E)5 hours
Q1: [generate python code] write python code to solve the problem, using math and sympy.
#1:
duration = Symbol('duration', positive=True)
delay = 30 / 60
total_distance = 600
original_speed = total_distance / duration
reduced_speed = total_distance / (duration + delay)
solution = solve_it(original_speed - reduced_speed - 200, duration)
ans = solution[duration]
print(ans)
Q2: [code execute] Execute the python code in #1 and get the value of "ans"
#2:
1.0
Q3: [compare] Which of the options among A)1 hour B)2 hours C)3 hours D)4 hours E)5 hours is most similar to the answer?
#3: A
Q4: [EOQ]
Ans: A
----
Description: Solve the following arithmetic problems on ratios and fractions, writing out intermediate arithmetic calculations as python code. Store your result as a variable named 'ans'.
Input: M men agree to purchase a gift for Rs. D. If 3 men drop out how much more will each have to contribute towards the purchase of the gift?  A)D/(M-3) B)MD/3 C)M/(D-3) D)3D/(M**2-3M) E)None of these
Q1: [generate python code] write python code to solve the problem, using math and sympy.
#1:
M = Symbol('M')
D = Symbol('D')
cost_before_dropout = D / M
cost_after_dropout = D / (M - 3)
ans=simplify(cost_after_dropout - cost_before_dropout)
print(ans)
Q2: [code execute] Execute the python code in #1 and get the value of "ans"
#2: 3*D/(M*(M - 3))
Q3: [compare] Which of the options among A)D/(M-3) B)MD/3 C)M/(D-3) D)3D/(M**2-3M) E)None of these is most similar to the answer?
#3: D
Q4: [EOQ]
Ans: D
----
Description: (Hindu Knowledge) Answer questions about Hindu mythology by choosing the option that best answers the question.
Input: In the Hindu epic Ramayana, the main villain was a devotee of which deity?
  choice: Indra
  choice: Vishnu
  choice: Brahma
  choice: Shiva
Q1: [search] In the Hindu epic Ramayana, who is the main villain?
#1: Ravana is the main antagonist of the Hindu Epic, the Ramayana.
Q2: [search] Ravana was a devotee of which deity?
#2: Ravana, was an ardent devotee of Lord Shiva, is depicted and described as a great scholar,a brahman,a capable ruler and a maestro of the Veena.
Q3: [compare] Which option is the answer in #3 most similar to?
#3: Shiva
Q4: [EOQ]
Ans: Shiva
----
Description: Solve the following middle-school arithmetic problems, writing out intermediate arithmetic calculations as python code. Store your result as a variable named 'ans'.
Input: Janet’s ducks lay 16 eggs per day. She eats three for breakfast every morning and bakes muffins for her friends every day with four. She sells the remainder at the farmers' market daily for $2 per fresh duck egg. How much in dollars does she make every day at the farmers' market?
Q1: [generate python code] write down the arithmetic or algebra equations as python code, storing the answer as 'ans'
#1:
total_eggs = 16
eaten_eggs = 3
baked_eggs = 4
sold_eggs = total_eggs - eaten_eggs - baked_eggs
dollars_per_egg = 2
ans = sold_eggs * dollars_per_egg
print(ans)
Q2: [code execute] Execute the python code in #1 and get the value of "ans"
#2: 18
Q3: [EOQ]
Ans:18
----
Description: Solve the following middle-school arithmetic problems, writing out intermediate arithmetic calculations as python code. Store your result as a variable named 'ans'.
Input: Every day, Wendi feeds each of her chickens three cups of mixed chicken feed, containing seeds, mealworms and vegetables to help keep them healthy.  She gives the chickens their feed in three separate meals. In the morning, she gives her flock of chickens 15 cups of feed.  In the afternoon, she gives her chickens another 25 cups of feed.  How many cups of feed does she need to give her chickens in the final meal of the day if the size of Wendi's flock is 20 chickens?
Q1: [generate python code] write down the arithmetic or algebra equations as python code, storing the answer as 'ans'
#1:
numb_of_chickens = 20
cups_for_each_chicken = 3
cups_for_all_chicken = num_of_chickens * cups_for_each_chicken
cups_in_the_morning = 15
cups_in_the_afternoon = 25
ans = cups_for_all_chicken - cups_in_the_morning - cups_in_the_afternoon
print(ans)
Q2: [code execute] Execute the python code in #1 and get the value of "ans"
#2: 20
Q3: [EOQ]
Ans: 20
----
Description: Solve the following middle-school arithmetic problems, writing out intermediate arithmetic calculations as python code. Store your result as a variable named 'ans'.
Input: Joseph and Getty went to buy ice creams, they together bought 36 ice creams. On the way back, Joseph ate 12 of the ice creasm, and he has 2 ice creams left now.
Q1: [generate python code] write down the arithmetic or algebra equations as python code, storing the answer as 'ans'
#1:
num_ice_creams_bought_by_joseph = 2 + 12
total_ice_creams = 36
ans = total_ice_creams - num_ice_creams_bought_by_joseph
print(ans)
Q2: [code execute] Execute the python code in #1 and get the value of "ans"
#2: 22
Q3: [EOQ]
Ans: 22
----
Description: {}
Input: {}
Q1: """


def get_few_shot_cot_prompt(task_name: str, description: str, strategy: str) -> str:
    if strategy == "fixed":
        return few_shot_cot_prompt
    elif strategy == "random":
        return random_tasks(N=6)
    elif strategy == "similar":
        return similar_tasks(description, io_pairs, N=6)
    elif strategy == "similar_auto_decomp":
        return similar_auto_breakdowns(description, io_pairs, N=6)
    elif strategy == "llm_similar":
        return llm_similar_tasks(task_name, description, io_pairs, N=6)


def few_shot_cot(temperature=0.3, model_name="text-davinci-002", strategy="fixed"):
    task_name = "Physics Questions"
    task_description = "(Physics Questions) Answer these high-school-level physics questions by applying the right physics formula, making substitutions, and storing the result in 'ans'."

    prompt = get_few_shot_cot_prompt(task_name, task_description, strategy)

    def predict(description, chunk):
        gpt3 = OpenAIModel(
            model=model_name, max_length=1000, temperature=temperature, quote="---", n=1
        )
        prompts = [prompt.format(description, x) for x in chunk]
        return gpt3(prompts)

    interpreter = TopDownVisitor(model_name=model_name, temperature=temperature)

    def predict_complete(description, chunk):
        gpt3 = OpenAIModel(
            model=model_name, max_length=1000, temperature=temperature, quote="---", n=1
        )
        prompts = [prompt.format(description, x) for x in chunk]
        outputs = gpt3(prompts)
        completed_outputs = [
            interpreter.complete_program(prefix, output) for prefix, output in zip(prompts, outputs)
        ]
        return completed_outputs

    perf_array = []
    runs = 5
    for run in range(runs):
        print(f"run {run+1}/{runs}")
        answers = []
        new_labels = [label.split(" ")[0] for label in labels]
        for x in tqdm(chunks(inputs, 10)):
            x = [ex.replace("\nA:", "") for ex in x]
            answers.extend(predict_complete(task_description, x))
        preds = [get_answer(x) for x in answers]
        perf_array.append(substring_match(new_labels, preds))
        print(perf_array)
    print("Few-shot COT performance:")
    print("Mean", np.mean(perf_array))
    print("Std. Dev", np.std(perf_array))


auto_cot_corrected_prompt = """(Physics Questions) Answer these high-school-level physics multiple-choice questions.
Q: Lamar Gant, U.S. powerlifting star, became the first man to deadlift five times his own body weight in 1985. Deadlifting involves raising a loaded barbell from the floor to a position above the head with outstretched arms. Determine the work done by Lamar in deadlifting 260 kg to a height of 0.85 m above the ground.
A: Let's think step-by-step.

The first step is to calculate the force that Lamar is exerting on the barbell. Since he is deadlifting 260 kg, we can say that the force he is exerting is mg, where m is 260 kg and g is the acceleration due to gravity.
Next, we need to calculate the distance that the barbell is being lifted. We know that it is being lifted to a height of 0.85 m above the ground, so the distance is simply 0.85 m.
Now that we have the force and the distance, we can calculate the work done by Lamar as follows:
Work = Force * Distance
Work = (mg) * 0.85 m
Work = (260 kg * 9.8 m/s^2) * 0.85 m
Work = 2165.8 J

The final answer is 2165.8 J
----
(Physics Questions) Answer these high-school-level physics multiple-choice questions.
Q: In the Funny Car competition at the Joliet Speedway in Joliet, Illinois in October of 2004, John Force complete the 1/4-mile dragster race in a record time of 4.437 seconds. Determine the average speed of the dragster in km/hr.
A: Let's think step-by-step.
First, we need to convert 4.437 seconds into hours. There are 3,600 seconds in one hour, so we divide 4.437 by 3,600 to get 0.0012325 hours.
Next, we need to convert 1/4 mile into kilometers. There are 1.609 kilometers in one mile, so we multiply 1/4 by 1.609 to get 0.40225 kilometers.
Finally, we calculate the average speed by dividing the distance traveled (0.40225 kilometers) by the time it took to travel that distance (0.0012325 hours). This gives us an average speed of 326.36 kilometers per hour.

The final answer is 326.4 km/hr
----
(Physics Questions) Answer these high-school-level physics multiple-choice questions.
Q: A bicycle has a momentum of 24 kg*m/s. What momentum would the bicycle have if it had one-half the mass and was moving with thrice the speed?
A: Let's think step-by-step.
We know that the bicycle's momentum is currently 24 kg*m/s. We also know the formula for Momentum.
Momentum = mass * speed
If the mass is one-half the original mass and a speed is thrice the original speed, the Momentum becomes:
New Momentum = 1/2 * mass * 3 * speed
For the new bicycle, that would be:
New Momentum = 3/2 * mass * speed
Thus, the new momentum is 3/2 of the original. That would be 3/2*24 = 36

The final answer is 36 kg*m/s
----
"""

auto_cot_cleaned_prompt = """(Physics Questions) Answer these high-school-level physics multiple-choice questions.
Q: Lamar Gant, U.S. powerlifting star, became the first man to deadlift five times his own body weight in 1985. Deadlifting involves raising a loaded barbell from the floor to a position above the head with outstretched arms. Determine the work done by Lamar in deadlifting 260 kg to a height of 0.85 m above the ground.
A: Let's think step-by-step.

The work done by Lamar in deadlifting 260 kg to a height of 0.85 m above the ground is:
$W = \frac{1}{2} \cdot 260 \cdot 9.8 \cdot 0.85 = 12,091 \text{ J}$

The final answer is 12,091 J.
----
(Physics Questions) Answer these high-school-level physics multiple-choice questions.
Q: In the Funny Car competition at the Joliet Speedway in Joliet, Illinois in October of 2004, John Force complete the 1/4-mile dragster race in a record time of 4.437 seconds. Determine the average speed of the dragster in km/hr.
A: Let's think step-by-step.

First, we need to figure out how long it took the dragster to travel 1/4 of a mile. Since we know the dragster's speed in terms of seconds, we can convert 1/4 of a mile into seconds. There are 5280 feet in a mile, so 1/4 of a mile is 1320 feet. There are 3 feet in a yard, so 1/4 of a mile is also 440 yards. There are 3600 seconds in an hour, so 1/4 of a mile is also 1.2 seconds.
Now that we know how long it took the dragster to travel 1/4 of a mile, we can calculate the average speed. The dragster traveled 1/4 of a mile in 1.2 seconds, so the average speed is 1/4 of a mile divided by 1.2 seconds. This is equal to 0.208 miles per second, or 0.208 * 3600 = 750 km/hr.

The final answer is 750 km/hr.
----
(Physics Questions) Answer these high-school-level physics multiple-choice questions.
Q: A bicycle has a momentum of 24 kg*m/s. What momentum would the bicycle have if it had one-half the mass and was moving with thrice the speed?
A: Let's think step-by-step.
The bicycle's momentum is 24 kg*m/s.
The bicycle has a mass of 12 kg and a speed of 6 m/s.
If the bicycle had one-half the mass, it would have a mass of 6 kg.
If the bicycle had thrice the speed, it would have a speed of 18 m/s.
Therefore, the bicycle would have a momentum of 6*18=108 kg*m/s.

The final answer is 108 kg*m/s.
----
"""


def auto_cot(
    temperature=0.3,
    model_name="text-davinci-002",
    predict=True,
    use_corrected=False,
    self_consistency=False,
):
    global auto_cot_cleaned_prompt
    global auto_cot_corrected_prompt
    auto_cot_prompt = ""
    description = (
        "(Physics Questions) Answer these high-school-level physics multiple-choice questions."
    )
    for io_pair in io_pairs:
        gpt3 = OpenAIModel(model=model_name, max_length=500, temperature=0.7, quote="---", n=1)
        prompt = description + "\n" + io_pair[0] + "\nA: Let's think step-by-step.\n"
        auto_cot_prompt += prompt
        cot = gpt3(prompt)
        auto_cot_prompt += cot[0] + "\n----\n"

    if use_corrected:
        auto_cot_prompt = auto_cot_corrected_prompt
    else:
        auto_cot_prompt = auto_cot_cleaned_prompt

    print(auto_cot_prompt)
    f = open("auto_cot_demonstrations.txt", "a+")
    f.write("Anachronisms\n\n")
    f.write(auto_cot_prompt)

    if not predict:
        return

    def predict_self_consistency(description, chunk, n=5):
        gpt3 = OpenAIModel(
            model=model_name, max_length=1000, temperature=temperature, quote="---", n=n
        )
        prompts = [
            auto_cot_prompt + task_description + "\n" + x + "\nA: Let's think step-by-step.\n"
            for x in chunk
        ]
        return gpt3(prompts)

    def predict(chunk):
        gpt3 = OpenAIModel(
            model=model_name, max_length=500, temperature=temperature, quote="---", n=1
        )
        prompts = [
            auto_cot_prompt + description + "\n" + x + "\nA: Let's think step-by-step.\n"
            for x in chunk
        ]
        return gpt3(prompts)

    perf_array = []
    runs = 5
    for run in range(runs):
        print(f"run {run+1}/{runs}")
        answers = []
        for x in tqdm(chunks(inputs, 10)):
            x = [ex.replace("\nA:", "") for ex in x]
            answers.extend(predict(x))
        preds = [get_autocot_answer(x) for x in answers]
        perf_array.append(substring_match(labels, preds))
        print(perf_array)
    print("Auto-CoT Performance:")
    print("Mean", np.mean(perf_array))
    print("Std. Dev", np.std(perf_array))


def affordance():
    def predict(description, chunk):
        gpt3 = OpenAIModel(model=model_name, max_length=2048, temperature=0.4, quote="---", n=1)
        prompts = [few_shot_cot_prompt % (description, x) for x in chunk]
        return gpt3(prompts)

    def string_index(sequence, position):
        char_list = []
        for word in sequence:
            character = word[position]
            char_list.append(character)
        return char_list

    def predict_with_affordance(description, chunk):
        gpt3 = OpenAIModel(model=model_name, max_length=2048, temperature=0.4, quote="---", n=1)
        prompts = [few_shot_cot_prompt % (description, x) for x in chunk]
        return gpt3(prompts)

    perf_array = []
    runs = 5
    for run in range(runs):
        print(f"run {run+1}/{runs}")
        answers = []
        new_answers = []
        for x in tqdm(chunks(inputs, 10)):
            x = [ex.replace("\nA:", "") for ex in x]
            answers = predict(task_description, x)
            affordance_inputs = [
                json.loads(a.strip().split("\n")[1].replace("#1: ", "")) for a in answers
            ]
            affordance_outputs = [string_index(inp, 2) for inp in affordance_inputs]
            x = [
                ex + a[: re.search("#2: ", a).span(0)[1]] + json.dumps(o)
                for ex, a, o in zip(x, answers, affordance_outputs)
            ]
            new_answers.extend(predict_with_affordance(task_description, x))
        preds = [[y.strip() for y in x.split("\n")] for x in new_answers]
        perf_array.append(token_match(labels, preds))
        print(perf_array)
    print("Few-shot COT performance:")
    print("Mean", np.mean(perf_array))
    print("Std. Dev", np.std(perf_array))


def nl_program(
    model_name="davinci-codex-002-msft",
    temperature=0.3,
    strategy="fixed",
    self_consistency=False,
):
    task_name = "Physics Questions"
    task_description = "(Physics Questions) Answer these high-school-level physics questions by applying the right physics formula, making substitutions, and storing the result in 'ans'."

    prompt = get_few_shot_cot_prompt(task_name, task_description, strategy)

    interpreter = TopDownVisitor(model_name)

    if self_consistency:
        n = 15
        runs = 2
        batch_size = 2
    else:
        n = 1
        runs = 1
        batch_size = 10

    model = OpenAIModel(model_name, quote="---", temperature=temperature, max_length=1000, n=n)

    # strip final "A:" from input text
    new_inputs = [ex.replace("\nA:", "") for ex in inputs]
    # strip units from answers
    new_labels = [label.split(" ")[0] for label in labels]

    perf_array = []
    with tqdm(total=runs * (len(inputs) // batch_size + (len(inputs) % batch_size > 0))) as pbar:
        for run in range(runs):
            pbar.set_description(f"run {run+1}/{runs}")

            answers = []

            for x in chunks(new_inputs, batch_size):
                prompts = [prompt.format(task_description, ex) for ex in x]
                completions = model(prompts)

                if not self_consistency:
                    # run the answers for all prompts at once
                    completions = interpreter.batch_visit(prompts, completions)
                    answers.extend(get_answer(x) for x in completions)
                else:
                    # run the answers for each prompt, counting the different answers
                    for prompt_id, completions in enumerate(chunks(completions, n)):
                        completions = interpreter.batch_visit(
                            [prompts[prompt_id]] * len(completions), completions
                        )
                        new_answers = (get_answer(comp) for comp in completions)

                        # find the most frequent answer, leaving out None
                        c = Counter(ans for ans in new_answers if ans is not None)
                        answers.append(c.most_common(1)[0][0])

                pbar.update(1)

            perf_array.append(substring_match(new_labels, answers))

    print("FS-CoT Performance:", perf_array)
    print("- mean:", np.mean(perf_array))
    print("- std dev:", np.std(perf_array))
    if not self_consistency:
        positive_calls = [len(stack_trace) >= 1 for stack_trace in interpreter.execution_details]
        positive_rate = sum(positive_calls) / len(positive_calls)
        print("- rate of affordance call", positive_rate)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model_name",
        type=str,
        choices=[
            "gpt-3.5-turbo",
            "text-davinci-002",
            "text-davinci-003",
            "code-davinci-002",
            "code-cushman-001",
            "davinci-codex-002-msft",
        ],
        default="gpt-3.5-turbo",
    )
    parser.add_argument("--temperature", type=float, default="0.3")
    parser.add_argument(
        "--inference_strategy",
        type=str,
        choices=[
            "dummy",
            "few_shot",
            "auto_cot",
            "cot_rollout",
            "few_shot_cot",
            "nl_program",
        ],
        default="few_shot",
    )
    parser.add_argument("--num_train_examples", type=int, default=10)
    parser.add_argument("--num_dev_examples", type=int, default=len(inputs))
    parser.add_argument("--self_consistency", default=False, action="store_true")
    parser.add_argument(
        "--selection_strategy",
        type=str,
        choices=["fixed", "random", "similar", "similar_auto_decomp", "llm_similar"],
        default="fixed",
    )

    args = parser.parse_args()

    print("query:", task_description)
    print("train examples:", len(train_inputs))
    print("dev examples:", len(inputs))

    inputs = inputs[: args.num_dev_examples]
    labels = labels[: args.num_dev_examples]

    if args.inference_strategy == "few_shot":
        few_shot_prompt = get_few_shot_prompt(train_inputs, train_labels, n=args.num_train_examples)
        print("Length of few-shot prompt", len(tokenizer(few_shot_prompt)["input_ids"]))
        few_shot(args.num_train_examples, args.temperature, args.model_name)
    elif args.inference_strategy == "auto_cot":
        auto_cot(
            args.temperature,
            args.model_name,
            predict=True,
            use_corrected=False,
            self_consistency=False,
        )
    elif args.inference_strategy == "few_shot_cot":
        few_shot_cot(args.temperature, args.model_name, strategy=args.selection_strategy)
    elif args.inference_strategy == "nl_program":
        nl_program(
            args.model_name,
            args.temperature,
            self_consistency=args.self_consistency,
            strategy=args.selection_strategy,
        )
