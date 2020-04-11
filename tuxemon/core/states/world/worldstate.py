# -*- coding: utf-8 -*-
#
# Tuxemon
# Copyright (C) 2014, William Edwards <shadowapex@gmail.com>,
#                     Benjamin Bean <superman2k5@gmail.com>
#
# This file is part of Tuxemon.
#
# Tuxemon is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Tuxemon is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Tuxemon.  If not, see <http://www.gnu.org/licenses/>.
#
# Contributor(s):
#
# William Edwards <shadowapex@gmail.com>
# Leif Theden <leif.theden@gmail.com>
#
# core.states.world Handles the world map and player movement.
#
#
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import logging

import pygame

from tuxemon.compat import Rect
from tuxemon.core import prepare, state, networking
from tuxemon.core import rumble
from tuxemon.core.map_view import MapView
from tuxemon.core.platform.const import buttons, events, intentions
from tuxemon.core.platform.events import PlayerInput
from tuxemon.core.session import local_session
from tuxemon.core.tools import nearest

logger = logging.getLogger(__name__)

direction_map = {
    intentions.UP: "up",
    intentions.DOWN: "down",
    intentions.LEFT: "left",
    intentions.RIGHT: "right",
}


class WorldState(state.State):
    """ The state responsible for the world game play
    """

    preloaded_maps = {}

    keymap = {
        buttons.UP: intentions.UP,
        buttons.DOWN: intentions.DOWN,
        buttons.LEFT: intentions.LEFT,
        buttons.RIGHT: intentions.RIGHT,
        buttons.A: intentions.INTERACT,
        buttons.B: intentions.RUN,
        buttons.START: intentions.WORLD_MENU,
        buttons.BACK: intentions.WORLD_MENU,
    }

    def __init__(self, *args, **kwargs):
        super(WorldState, self).__init__(*args, **kwargs)
        self.allow_player_movement = None
        self.wants_to_move_player = None
        self.current_music = {"status": "stopped", "song": None, "previoussong": None}
        self.rumble_manager = rumble.RumbleManager()
        self.rumble = self.rumble_manager.rumbler

    def startup(self, *args, **kwargs):
        self.world = kwargs["world"]
        self.player_npc = None
        self.wants_to_move_player = None
        self.allow_player_movement = True

        ######################################################################
        #                            Transitions                             #
        ######################################################################

        # default variables for transition
        self.transition_alpha = 0
        self.transition_surface = None
        self.in_transition = False

        # The delayed teleport variable is used to perform a teleport in the
        # middle of a transition. For example, fading to black, then
        # teleporting the player, and fading back in again.
        self.delayed_teleport = False

        # The delayed facing variable used to change the player's facing in
        # the middle of a transition.
        self.delayed_facing = None
        self.view = MapView(self.world)
        self.view1 = MapView(self.world)
        self.player_npc = kwargs.get("player")
        self.set_player_npc(self.player_npc)

    def resume(self):
        """ Called after returning focus to this state
        """
        self.unlock_controls()

    def pause(self):
        """ Called before another state gets focus
        """
        self.lock_controls()
        self.stop_player()

    def set_player_npc(self, entity):
        """ Set the npc which is controlled

        :param entity:
        :return:
        """
        self.player_npc = entity
        self.view.follow(entity)

    def fade_and_teleport(self, duration=2):
        """ Fade out, teleport, fade in

        :return:
        """

        def cleanup():
            self.in_transition = False

        def fade_in():
            self.trigger_fade_in(duration)
            self.task(cleanup, duration)

        # cancel any fades that may be going one
        self.remove_animations_of(self)
        self.remove_animations_of(cleanup)

        self.stop_player()

        self.in_transition = True
        self.trigger_fade_out(duration)

        task = self.task(self.handle_delayed_teleport, duration)
        task.chain(fade_in, duration + .5)

    def trigger_fade_in(self, duration=2):
        """ World state has own fade code b/c moving maps doesn't change state

        :returns: None
        """
        self.set_transition_surface()
        self.animate(self, transition_alpha=0, initial=255, duration=duration, round_values=True)
        self.task(self.unlock_controls, duration - .5)  # unlock controls before fade ends

    def trigger_fade_out(self, duration=2):
        """ World state has own fade code b/c moving maps doesn't change state

        * will cause player to teleport if set somewhere else

        :returns: None
        """
        self.set_transition_surface()
        self.animate(self, transition_alpha=255, initial=0, duration=duration, round_values=True)
        self.stop_player()
        self.lock_controls()

    def handle_delayed_teleport(self):
        """ Call to teleport player if delayed_teleport is set

        * load a map
        * move player
        * send data to network about teleport

        :return: None
        """
        if self.delayed_teleport:
            self.stop_player()
            self.lock_controls()

            # check if map has changed, and if so, change it
            map_name = prepare.fetch("maps", self.delayed_mapname)

            # TODO: change player map

            self.player_npc.set_position((self.delayed_x, self.delayed_y))

            if self.delayed_facing:
                self.player_npc.facing = self.delayed_facing
                self.delayed_facing = None

            self.delayed_teleport = False

    def set_transition_surface(self, color=(0, 0, 0)):
        self.transition_surface = pygame.Surface(self.game.screen.get_size())
        self.transition_surface.fill(color)

    def draw(self, surface):
        """ Draw the game world to the screen

        :param surface:
        :return:
        """
        w, h = surface.get_size()

        r0 = Rect(0, 0, w / 2, h)
        r1 = Rect(w / 2, 0, w / 2, h)

        entity = local_session.world.get_entity("professor")
        if entity:
            entity.map_name = local_session.player.map_name
            entity.map = local_session.player.map
            self.view1.follow(entity)
        surface.fill(0)
        self.view.draw(r0, surface)
        self.view1.draw(r1, surface)
        self.fullscreen_animations(surface)

    def translate_input_event(self, event):
        """ Translate a key to a game input

        :type event: tuxemon.core.input.events.PlayerInput
        :rtype: tuxemon.core.input.events.PlayerInput
        """
        try:
            return PlayerInput(self.keymap[event.button], event.value, event.hold_time)
        except KeyError:
            pass

        if event.button == events.UNICODE:
            if event.value == "n":
                return PlayerInput(intentions.NOCLIP, event.value, event.hold_time)

        return event

    def process_event(self, event):
        """ Handles player input events. This function is only called when the
        player provides input such as pressing a key or clicking the mouse.

        Since this is part of a chain of event handlers, the return value
        from this method becomes input for the next one.  Returning None
        signifies that this method has dealt with an event and wants it
        exclusively.  Return the event and others can use it as well.

        You should return None if you have handled input here.

        :type event: core.input.PlayerInput
        :rtype: Optional[core.input.PlayerInput]
        """
        event = self.translate_input_event(event)

        if event.button == intentions.WORLD_MENU:
            if event.pressed:
                # logger.info("Opening main menu!")
                # self.game.release_controls()
                # self.game.push_state("WorldMenuState")
                if self.player_npc == local_session.player:
                    self.player_npc = local_session.world.get_entity("professor")
                    self.player_npc.map = local_session.player.map
                else:
                    self.player_npc = local_session.player
                return

        # map may not have a player registered
        if self.player_npc is None:
            return

        if event.button == intentions.INTERACT:
            if event.pressed:
                multiplayer = False
                if multiplayer:
                    self.check_interactable_space()
                    return

        if event.button == intentions.RUN:
            if event.held:
                self.player_npc.moverate = self.game.config.player_runrate
            else:
                self.player_npc.moverate = self.game.config.player_walkrate

        # If we receive an arrow key press, set the facing and
        # moving direction to that direction
        direction = direction_map.get(event.button)
        if direction is not None:
            if event.held:
                self.wants_to_move_player = direction
                if self.allow_player_movement:
                    self.move_player(direction)
                return
            elif not event.pressed:
                if direction == self.wants_to_move_player:
                    self.stop_player()
                    return

        if prepare.DEV_TOOLS:
            if event.pressed and event.button == intentions.NOCLIP:
                self.player_npc.ignore_collisions = not self.player_npc.ignore_collisions
                return

        # if we made it this far, return the event for others to use
        return event

    def lock_controls(self):
        """ Prevent input from moving the player

        :return:
        """
        self.allow_player_movement = False

    def unlock_controls(self):
        """ Allow the player to move

        If the player was previously holding a direction down,
        then the player will start moving after this is called.

        :return:
        """
        self.allow_player_movement = True
        if self.wants_to_move_player:
            self.move_player(self.wants_to_move_player)

    def stop_player(self):
        """ Reset controls and stop player movement at once.  Do not lock controls

        If player was in a movement then complete it before stopping.

        :return:
        """
        self.wants_to_move_player = None
        self.game.release_controls()
        if self.player_npc is not None:
            self.player_npc.cancel_movement()

    def stop_and_reset_player(self):
        """ Reset controls, stop player and abort movement.  Do not lock controls.

        Movement is aborted here, so the player will not complete movement
        to a tile.  It will be reset to the tile where movement started.

        Use if you don't want to trigger another tile event.

        :return:
        """
        self.wants_to_move_player = None
        self.game.release_controls()
        if self.player_npc is not None:
            self.player_npc.abort_movement()

    def move_player(self, direction):
        """ Move player in a direction.  Changes facing.

        :param direction:
        :return:
        """
        if self.player_npc is not None:
            self.player_npc.move_direction = direction

    # Below is the boneyard.  Eventually this should be added back in.

    def init_cinematics(self):
        ######################################################################
        #                       Fullscreen Animations                        #
        ######################################################################

        # The cinema bars are used for cinematic moments.
        # The cinema state can be: "off", "on", "turning on" or "turning off"
        self.cinema_state = "off"
        self.cinema_speed = 15 * prepare.SCALE  # Pixels per second speed of the animation.

        self.cinema_top = {}
        self.cinema_bottom = {}

        # Create a surface that we'll use as black bars for a cinematic
        # experience
        self.cinema_top['surface'] = pygame.Surface(
            (self.resolution[0], self.resolution[1] / 6))
        self.cinema_bottom['surface'] = pygame.Surface(
            (self.resolution[0], self.resolution[1] / 6))

        # Fill our empty surface with black
        self.cinema_top['surface'].fill((0, 0, 0))
        self.cinema_bottom['surface'].fill((0, 0, 0))

        # When cinema mode is off, this will be the position we'll draw the
        # black bar.
        self.cinema_top['off_position'] = [
            0, -self.cinema_top['surface'].get_height()]
        self.cinema_bottom['off_position'] = [0, self.resolution[1]]
        self.cinema_top['position'] = list(self.cinema_top['off_position'])
        self.cinema_bottom['position'] = list(
            self.cinema_bottom['off_position'])

        # When cinema mode is ON, this will be the position we'll draw the
        # black bar.
        self.cinema_top['on_position'] = [0, 0]
        self.cinema_bottom['on_position'] = [
            0, self.resolution[1] - self.cinema_bottom['surface'].get_height()]

    def midscreen_animations(self, surface):
        """Handles midscreen animations that will be drawn UNDER menus and dialog.

        NOTE: BROKEN

        :param surface: surface to draw on

        :rtype: None
        :returns: None

        """
        raise RuntimeError("deprecated.  refactor!")

        if self.cinema_state == "turning on":

            self.cinema_top['position'][
                1] += self.cinema_speed * self.time_passed_seconds
            self.cinema_bottom['position'][
                1] -= self.cinema_speed * self.time_passed_seconds

            # If we've reached our target position, stop the animation.
            if self.cinema_top['position'] >= self.cinema_top['on_position']:
                self.cinema_top['position'] = list(
                    self.cinema_top['on_position'])
                self.cinema_bottom['position'] = list(
                    self.cinema_bottom['on_position'])

                self.cinema_state = "on"

            # Draw the cinema bars
            surface.blit(
                self.cinema_top['surface'], self.cinema_top['position'])
            surface.blit(
                self.cinema_bottom['surface'], self.cinema_bottom['position'])

        elif self.cinema_state == "on":
            # Draw the cinema bars
            surface.blit(
                self.cinema_top['surface'], self.cinema_top['position'])
            surface.blit(
                self.cinema_bottom['surface'], self.cinema_bottom['position'])

        elif self.cinema_state == "turning off":

            self.cinema_top['position'][1] -= (
                    self.cinema_speed * self.time_passed_seconds)
            self.cinema_bottom['position'][
                1] += self.cinema_speed * self.time_passed_seconds

            # If we've reached our target position, stop the animation.
            if self.cinema_top['position'][1] <= self.cinema_top['off_position'][1]:
                self.cinema_top['position'] = list(
                    self.cinema_top['off_position'])
                self.cinema_bottom['position'] = list(
                    self.cinema_bottom['off_position'])

                self.cinema_state = "off"

            # Draw the cinema bars
            surface.blit(
                self.cinema_top['surface'], self.cinema_top['position'])
            surface.blit(
                self.cinema_bottom['surface'], self.cinema_bottom['position'])

    ####################################################
    #         Full Screen Animations Functions         #
    ####################################################
    def fullscreen_animations(self, surface):
        """Handles fullscreen animations such as transitions, cutscenes, etc.

        :param surface: Surface to draw onto

        :rtype: None
        :returns: None

        """
        if self.in_transition:
            self.transition_surface.set_alpha(self.transition_alpha)
            surface.blit(self.transition_surface, (0, 0))

    def check_interactable_space(self):
        """Checks to see if any Npc objects around the player are interactable. It then populates a menu
        of possible actions.

        :param: None

        :rtype: Bool
        :returns: True if there is an Npc to interact with.

        """
        collision_dict = self.player_npc.get_collision_map(self)
        player_tile_pos = nearest(self.player_npc.tile_pos)
        collisions = self.player_npc.collision_check(player_tile_pos, collision_dict, self.collision_lines_map)
        if not collisions:
            pass
        else:
            for direction in collisions:
                if self.player_npc.facing == direction:
                    if direction == "up":
                        tile = (player_tile_pos[0], player_tile_pos[1] - 1)
                    elif direction == "down":
                        tile = (player_tile_pos[0], player_tile_pos[1] + 1)
                    elif direction == "left":
                        tile = (player_tile_pos[0] - 1, player_tile_pos[1])
                    elif direction == "right":
                        tile = (player_tile_pos[0] + 1, player_tile_pos[1])
                    for npc in self.npcs.values():
                        tile_pos = (int(round(npc.tile_pos[0])), int(round(npc.tile_pos[1])))
                        if tile_pos == tile:
                            logger.info("Opening interaction menu!")
                            self.game.push_state("InteractionMenu")
                            return True
                        else:
                            continue

    def handle_interaction(self, event_data, registry):
        """Presents options window when another player has interacted with this player.

        :param event_data: Information on the type of interaction and who sent it.
        :param registry:

        :type event_data: Dictionary
        :type registry: Dictionary

        :rtype: None
        :returns: None
        """
        target = registry[event_data["target"]]["sprite"]
        target_name = str(target.name)
        networking.update_client(target, event_data["char_dict"], self.game)
        if event_data["interaction"] == "DUEL":
            if not event_data["response"]:
                self.interaction_menu.visible = True
                self.interaction_menu.interactable = True
                self.interaction_menu.player = target
                self.interaction_menu.interaction = "DUEL"
                self.interaction_menu.menu_items = [target_name + " would like to Duel!", "Accept", "Decline"]
            else:
                if self.wants_duel:
                    if event_data["response"] == "Accept":
                        world = self.game.current_state
                        pd = world.player1.__dict__
                        event_data = {"type": "CLIENT_INTERACTION",
                                      "interaction": "START_DUEL",
                                      "target": [event_data["target"]],
                                      "response": None,
                                      "char_dict": {"monsters": pd["monsters"],
                                                    "inventory": pd["inventory"]
                                                    }

                                      }
                        self.game.server.notify_client_interaction(cuuid, event_data)
