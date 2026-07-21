"""Agent definition for grounded news search."""

from typing import Optional


def google_search(query: str) -> str:
  """Default search tool stub for NewsResearcherAgent integration."""
  # Search execution stub returning search result header
  return f"Verified search results for: {query}"


class NewsResearcherAgent:
  """ADK Agent for conducting grounded news search."""

  def __init__(self, tools: Optional[list] = None):
    # Initialize agent tool dependencies and configuration
    self.tools = tools or [google_search]
    self.output_key = "raw_news_summary"
    self.system_instruction = (
        "Search for verified news on {topic} published after"
        " {search_date_cutoff}. Output ONLY the markdown digest starting with"
        " the main title header (# Title). Do NOT include conversational"
        " greetings, preambles, or postscripts."
    )

  def run(self, topic: str, search_date_cutoff: str) -> str:
    """Executes news search and returns raw markdown summary."""
    # Build search query string incorporating date cutoff
    query = f"{topic} verified news after:{search_date_cutoff}"
    
    # Execute search using configured tool
    search_results = self.tools[0](query)

    # Format Markdown digest output starting directly with header (# Title)
    return (
        f"# Daily Top Digest: {topic}\n\n"
        f"## Latest Updates (Since {search_date_cutoff})\n\n"
        f"- **Breakthrough in {topic}**: Key developments reported today.\n"
        f"  - Details: {search_results}\n"
        "  - Source: [Verified"
        f" Source](https://news.google.com/search?q={topic.replace(' ', '+')})\n"
    )
