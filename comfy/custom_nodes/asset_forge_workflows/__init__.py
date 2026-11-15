"""Asset Forge: expõe os workflows enviados pelo Asset Forge como "templates" do ComfyUI.

Não registra nós. O ComfyUI serve a pasta example_workflows/ deste módulo em
/api/workflow_templates/asset_forge_workflows/<nome>.json, e o frontend abre um deles com
/?template=<nome>&source=asset_forge_workflows (botão "Abrir no ComfyUI" do menu Workflows do Asset Forge).
"""
NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}
