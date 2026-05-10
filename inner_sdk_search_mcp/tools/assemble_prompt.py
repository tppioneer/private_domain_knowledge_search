"""assemble_prompt 工具处理函数。"""

from __future__ import annotations

from ..models.schemas import AssemblePromptInput, AssemblePromptOutput
from ..prompt.assemble_prompt import assemble_prompt as run_assemble_prompt


async def assemble_prompt_tool(
    input_: AssemblePromptInput,
) -> AssemblePromptOutput:
    return await run_assemble_prompt(input_)
