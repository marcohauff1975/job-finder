# CrewAI Starter

A minimal two-agent CrewAI project (Researcher + Writer).

## Setup

```bash
cd crewai-starter
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your real OPENAI_API_KEY
```

## Run

```bash
python main.py
```

## Files

- `main.py` — defines the agents, tasks, and crew
- `requirements.txt` — dependencies
- `.env.example` — copy to `.env` and add your OpenAI API key
