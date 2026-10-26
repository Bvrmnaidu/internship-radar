"""AI engine: read a job description, score it against cv.json, draft an application.

Pipeline:  extract.fetch_jd_text -> match.analyze -> generate.cover_letter
Everything is optional: with no ANTHROPIC_API_KEY the scraper runs exactly as before.
"""
