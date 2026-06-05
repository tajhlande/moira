# Standard Tools

The following is a descriptive list of standard tools that should be built into MOiRA 
and generally always available to the agent.


## User question tool

If the intelligence model wants to ask the user a follow-up question to gain clarity about 
the research question or how to formulate an answer, this tool allows the model
to ask a question to the user.  The model should be directed to pose a question in
multiple choice format, with A/B/C/D options that the UI can use to make user response
really easy, and there should also be an option for the user to provide a free-text response.

The question and the user's answer should be appended to the prompt for the graph step,
and the step re-run.

## Web search tool

If the intelligence model wishes to search the web for information about specific
topics, the tool should allow a search query to be sent, and receive back a
sorted list of URLs and relevance scores.  

Optional parameter (list[str]): a list of web domains to search, if the tool knows
what websites it should be searching. 

Optional parameter (int, default 5): the maximum number of search results to return. 

## URL content retrieval tool

Given a URL, the tool retrieves the content of the page. 

Optional parameter (boolean, default true): return only the text content of a page

Optional parameter (str): XPath to return only a subset of content

Optional parameter (boolean, default false): summarize the content via a sub-agent

### Implementation notes

The beautiful soup module can provide some of the needed capabilities.

## Calculator

Given an input string containing a mathematical expression in infix notation, compute the expression provided
and return the result.

This requires a short grammar to include only:

* Positive, negative, and zero integer literals
* Decimal formatted floating point literals
* Scientific notation floating point literals
* Arithmetic operators: + - * / %
* Some standard math library functions: power(x, n), sqrt(x), root(x, n), exp(x), ln(x), log(x, n), abs(x), floor(x), ceil(x), round(x)
* trig functions in radians: sin(x), cos(x), tan(x), sec(x), cosec(x), cotan(x), and their inverses
* parentheses for prioritization
* etc

In no case should we allow non-mathematical functions with side effects (other than throwing errors),
string literals, or variables.   

### Implementation notes

The pyparsing library's infix_math_parser could be used to sanitize math expressions here for 
safe resolution with python's `eval()`.


