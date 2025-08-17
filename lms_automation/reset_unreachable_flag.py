from lms_automation.app import app, db, Player

with app.app_context():
    player = Player.query.filter_by(name="A. Sirignano").first()
    if player:
        player.unreachable = False
        db.session.commit()
        print("Player A. Sirignano's unreachable flag has been reset.")
    else:
        print("Player A. Sirignano not found.")
