from flask import Blueprint, jsonify, make_response, request

from app.cart import load_cart, save_cart

bp = Blueprint("cart", __name__)


@bp.route("/cart")
def show_cart():
    return jsonify(load_cart())


@bp.route("/cart/items", methods=["POST"])
def add_item():
    cart = load_cart()
    cart["items"].append(request.json)
    return save_cart(make_response(jsonify(cart)), cart)
