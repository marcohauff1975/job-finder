"""
CrewAI starter project.

Two agents collaborate on a task:
- Researcher: gathers key points on a topic
- Writer: turns the research into a short summary

Run:
    python main.py
"""

import os
from crewai import Agent, Task, Crew, Process, LLM

# CrewAI reads ANTHROPIC_API_KEY from the environment (loaded from .env below).
from dotenv import load_dotenv
load_dotenv()

TOPIC = "the benefits of using AI agents to automate repetitive business tasks"

# Claude model via Anthropic. Swap for "anthropic/claude-opus-4-8" or
# "anthropic/claude-haiku-4-5-20251001" if you want a different tier.
claude = LLM(model="anthropic/claude-sonnet-5")

# --- Agents -----------------------------------------------------------

researcher = Agent(
    role="Senior Researcher",
    goal=f"Uncover the most important, up-to-date points about: {TOPIC}",
    backstory=(
        "You are a meticulous researcher who distills complex topics into "
        "clear, well-organized bullet points."
    ),
    llm=claude,
    verbose=True,
)

writer = Agent(
    role="Content Writer",
    goal="Turn research notes into a clear, engaging short summary",
    backstory=(
        "You are a skilled writer who takes raw research and produces "
        "polished, easy-to-read prose for a general audience."
    ),
    llm=claude,
    verbose=True,
)

# --- Tasks --------------------------------------------------------------

research_task = Task(
    description=f"Research and list the 5 most important points about: {TOPIC}",
    expected_output="A bullet list of 5 key points, each 1-2 sentences.",
    agent=researcher,
)

writing_task = Task(
    description="Using the research notes, write a short 3-paragraph summary suitable for a blog post.",
    expected_output="A 3-paragraph summary in plain, engaging prose.",
    agent=writer,
    context=[research_task],
)

# --- Crew -----------------------------------------------------------------

crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, writing_task],
    process=Process.sequential,
    verbose=True,
)

if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
        )

    result = crew.kickoff()
    print("\n\n=== FINAL OUTPUT ===\n")
    print(result)
