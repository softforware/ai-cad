"""Claude tool-use loop: turns plain-language requests into calls to the CAD layer."""

import anthropic

import cad

MODEL = "claude-opus-5-5"

SYSTEM = """You are the CAD assistant inside a parametric CAD app. Units are millimetres.

You build parts by calling tools, never by describing geometry in prose:
- create_spur_gear: for any spur gear. Prefer it over custom code; its involute math is tested.
- create_custom_part: for any other shape. Write build123d (Python) code and assign the final
  solid to `result`. `from build123d import *` is already done. Example:
  result = Box(40, 20, 5) - Pos(10, 0, 0) * Cylinder(3, 10)

Rules:
- Fill sensible engineering defaults for unspecified values (20° pressure angle, face width
  about 8-10x module) and say which defaults you chose.
- If the user gives pitch diameter instead of module, derive module = pitch_diameter / teeth.
- Check each tool result: valid_solid must be true and the dimensions must match the request.
  If a tool returns an error, fix the inputs and call it again.
- For edits ("make it 30 teeth"), call the tool again with the full updated parameters.
- After building, reply in 1-3 short sentences: what you made plus the key derived dimensions."""

TOOLS = [
    {
        "name": "create_spur_gear",
        "description": "Create a standard involute spur gear (no profile shift), optionally with "
                       "a bore, keyway and hub. Returns derived dimensions and validity checks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short part name"},
                "module": {"type": "number", "description": "Gear module in mm"},
                "teeth": {"type": "integer", "description": "Number of teeth (>= 8)"},
                "pressure_angle": {"type": "number", "description": "Degrees, usually 20"},
                "face_width": {"type": "number", "description": "Gear thickness in mm"},
                "bore_diameter": {"type": "number", "description": "Center hole mm, 0 for none"},
                "keyway_width": {"type": "number", "description": "mm, 0 for none"},
                "keyway_depth": {"type": "number", "description": "Depth past the bore, mm"},
                "hub_diameter": {"type": "number", "description": "mm, 0 for none"},
                "hub_length": {"type": "number", "description": "mm protruding from one face"},
            },
            "required": ["name", "module", "teeth", "pressure_angle", "face_width"],
        },
    },
    {
        "name": "create_custom_part",
        "description": "Build any other part by running build123d Python code in a sandbox. "
                       "The code must assign the final solid to `result`.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "code": {"type": "string", "description": "build123d code assigning `result`"},
            },
            "required": ["name", "code"],
        },
    },
]

GEAR_KEYS = ["module", "teeth", "pressure_angle", "face_width", "bore_diameter",
             "keyway_width", "keyway_depth", "hub_diameter", "hub_length"]


def build_gear(params: dict) -> dict:
    kwargs = {k: params[k] for k in GEAR_KEYS if params.get(k) is not None}
    shape, derived = cad.make_spur_gear(**kwargs)
    part = cad.save_part(shape, params.get("name") or "Spur gear")
    return {**part, "kind": "spur_gear", "params": kwargs, "derived": derived}


def run_tool(name: str, args: dict) -> dict:
    if name == "create_spur_gear":
        return build_gear(args)
    if name == "create_custom_part":
        part = cad.run_custom_code(args["code"], args.get("name") or "Custom part")
        return {**part, "kind": "custom", "code": args["code"]}
    raise ValueError(f"unknown tool {name}")


class Session:
    """One conversation with Claude; history is kept so follow-up edits work."""

    def __init__(self):
        self.client = anthropic.Anthropic()
        self.messages: list = []

    def chat(self, text: str) -> dict:
        self.messages.append({"role": "user", "content": text})
        parts = []
        for _ in range(8):  # cap tool round-trips per request
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM,
                tools=TOOLS,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=self.messages,
            )
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                return {"reply": "The model declined this request.", "parts": parts}
            if response.stop_reason != "tool_use":
                reply = "\n".join(b.text for b in response.content if b.type == "text")
                return {"reply": reply, "parts": parts}

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    part = run_tool(block.name, block.input)
                    parts.append(part)
                    summary = {k: v for k, v in part.items() if k not in ("stl", "step", "code")}
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": str(summary)})
                except Exception as e:
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": f"Error: {e}", "is_error": True})
            self.messages.append({"role": "user", "content": results})
        return {"reply": "Stopped after too many tool calls.", "parts": parts}
