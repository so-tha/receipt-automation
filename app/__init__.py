from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from app.config import get_config
from app.models import db, User
from app.routes import register_blueprints
from sqlalchemy import text, inspect
import os
from pathlib import Path


def create_app():
    config = get_config()
    
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config.from_object(config)
    
    db_path = app.config.get('SQLALCHEMY_DATABASE_URI', 'sqlite:///loglife.db')
    
    if db_path.startswith('sqlite:///'):
        db_file = db_path.replace('sqlite:///', '')
        db_dir = os.path.dirname(db_file)
        
        if db_dir and not db_dir.startswith(':'):
            Path(db_dir).mkdir(parents=True, exist_ok=True)
    
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    db.init_app(app)
    
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Por favor, faça login para continuar'
    
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(user_id)
    
    register_blueprints(app)
    
    with app.app_context():
        db.create_all()
        _run_flask_migrations()
    
    return app


def _run_flask_migrations():
    """Run database migrations to add missing columns."""
    from sqlalchemy import inspect
    
    inspector = inspect(db.engine)
    
    if 'reports' in inspector.get_table_names():
        columns = {col['name'] for col in inspector.get_columns('reports')}
        
        if 'is_locked' not in columns:
            db.session.execute(text('ALTER TABLE reports ADD COLUMN is_locked BOOLEAN DEFAULT 0'))
            db.session.commit()
            print("✓ Adicionada coluna 'is_locked' à tabela 'reports'")
        
        if 'extracted_data' not in columns:
            db.session.execute(text('ALTER TABLE reports ADD COLUMN extracted_data TEXT'))
            db.session.commit()
            print("✓ Adicionada coluna 'extracted_data' à tabela 'reports'")
        
        if 'file_hash' not in columns:
            db.session.execute(text('ALTER TABLE reports ADD COLUMN file_hash VARCHAR(64)'))
            db.session.commit()
            print("✓ Adicionada coluna 'file_hash' à tabela 'reports'")
