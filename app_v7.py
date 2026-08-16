from flask import jsonify, request
from app import app
from v7_pipeline import V7Error, render, validate_content
from v8.routes import bp as v8_blueprint

app.register_blueprint(v8_blueprint)

@app.post("/v7/validate")
def validate():
    body=request.get_json(silent=True) or {}; errors=validate_content(body.get("content",{}),bool(body.get("require_publish_gate",True)))
    return jsonify({"valid":not errors,"errors":errors}),(200 if not errors else 422)

@app.post("/v7/render")
def render_route():
    body=request.get_json(silent=True) or {}
    try: return jsonify(render(body.get("content",{}),body.get("assets",{}),body.get("options",{})))
    except V7Error as exc: return jsonify({"error":str(exc)}),422
    except Exception as exc: return jsonify({"error":str(exc)}),500

if __name__=="__main__": app.run(host="0.0.0.0",port=10000)
