"""Local entry point for Running Coach v2."""
from web.app import app

if __name__ == "__main__":
    app.run(debug=True, port=5000)
