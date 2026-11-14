import os
from dotenv import load_dotenv
from langchain_aws import ChatBedrockConverse
from langchain_core.tools import tool

load_dotenv()
llm = ChatBedrockConverse(model=os.environ["BEDROCK_MODEL_ID"], region_name=os.environ["AWS_REGION"])

# Test 1: plain chat
print(llm.invoke("Say hi in five words.").content)

# Test 2: tool calling (the agent depends on this)
@tool
def get_order(product: str) -> str:
    """Return the recommended order quantity for a product."""
    return f"{product}: order 50 boxes"

reply = llm.bind_tools([get_order]).invoke("How many boxes of Schezwan noodles should I order?")
print(reply.tool_calls)