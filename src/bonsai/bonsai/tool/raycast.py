# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2022 Cyril Waechter <cyril@biminsight.ch>
#
# This file is part of Bonsai.
#
# Bonsai is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Bonsai is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Bonsai.  If not, see <http://www.gnu.org/licenses/>.

import bmesh
import bpy
from bpy_extras import view3d_utils
import bonsai.core.tool
import bonsai.tool as tool
import math
import mathutils
from mathutils import Matrix, Vector


class Raycast(bonsai.core.tool.Raycast):
    @classmethod
    def get_visible_objects(cls, context):
        depsgraph = context.evaluated_depsgraph_get()
        all_objs = []
        for dup in depsgraph.object_instances:
            if dup.is_instance:  # Real dupli instance
                obj = dup.instance_object
                all_objs.append(obj)
            else:  # Usual object
                obj = dup.object
                all_objs.append(obj)
        return all_objs

    @classmethod
    def get_on_screen_2d_bounding_boxes(cls, context, obj):
        obj_matrix = obj.matrix_world.copy()
        bbox = [obj_matrix @ Vector(v) for v in obj.bound_box]

        transposed_bbox = []
        bbox_2d = []

        for v in bbox:
            coord_2d = view3d_utils.location_3d_to_region_2d(context.region, context.space_data.region_3d, v)
            if coord_2d is not None:
                transposed_bbox.append(coord_2d)

        region = context.region
        borders = (region.width, region.height)
        for i, axis in enumerate(zip(*transposed_bbox)):
            if all(ax < 0 or ax > borders[i] for ax in axis):  # Filter only objects in viewport
                return (obj, None)
            min_point = min(axis)
            max_point = max(axis)
            bbox_2d.extend([min_point, max_point])

        return (obj, bbox_2d)

    @classmethod
    def intersect_mouse_2d_bounding_box(cls, mouse_pos, bbox, offset=None):
        x, y = mouse_pos
        xmin, xmax, ymin, ymax = bbox

        # extends bbox boundaries to improve snap
        if offset: 
            xmin -= offset
            xmax += offset
            ymin -= offset
            ymax += offset

        if xmin < x < xmax and ymin < y < ymax:
            return True
        else:
            return False

    @classmethod
    def get_viewport_ray_data(cls, context, event, mouse_pos=None):
        region = context.region
        rv3d = context.region_data
        if not mouse_pos:
            mouse_pos = event.mouse_region_x, event.mouse_region_y

        view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse_pos)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse_pos)
        ray_target = ray_origin + view_vector
        ray_direction = ray_target - ray_origin

        return ray_origin, ray_target, ray_direction

    @classmethod
    def get_object_ray_data(cls, context, event, obj_matrix, mouse_pos=None):
        if mouse_pos:
            ray_origin, ray_target, _ = cls.get_viewport_ray_data(context, event, mouse_pos)
        else:
            ray_origin, ray_target, _ = cls.get_viewport_ray_data(context, event)
        matrix_inv = obj_matrix.inverted()
        ray_origin_obj = matrix_inv @ ray_origin
        ray_target_obj = matrix_inv @ ray_target
        ray_direction_obj = ray_target_obj - ray_origin_obj

        return ray_origin_obj, ray_target_obj, ray_direction_obj

    @classmethod
    def obj_ray_cast(cls, context, event, obj, mouse_pos=None):
        if mouse_pos:
            ray_origin_obj, _, ray_direction_obj = cls.get_object_ray_data(
                context, event, obj.matrix_world.copy(), mouse_pos
            )
        else:
            ray_origin_obj, _, ray_direction_obj = cls.get_object_ray_data(context, event, obj.matrix_world.copy())
        success, location, normal, face_index = obj.ray_cast(ray_origin_obj, ray_direction_obj)
        if success:
            return location, normal, face_index
        else:
            return None, None, None

    @classmethod
    def ray_cast_by_proximity(cls, context, event, obj, face=None):
        region = context.region
        rv3d = context.region_data
        mouse_pos = event.mouse_region_x, event.mouse_region_y
        ray_origin, ray_target, ray_direction = cls.get_viewport_ray_data(context, event)
        points = []

        # Makes the snapping point more or less sticky then others
        # It changes the distance and affects how the snapping point is sorted
        stick_factor = 0.02

        try:
            loc = view3d_utils.region_2d_to_location_3d(region, rv3d, mouse_pos, ray_direction)
        except:
            loc = Vector((0, 0, 0))

        bm = bmesh.new()
        if face is None:  # Object with faces
            bm.from_mesh(obj.data)
        else:  # Object without faces
            verts = [bm.verts.new(obj.data.vertices[i].co) for i in face.vertices]
            bm.faces.new(verts)

        for vertex in bm.verts:
            world_vertex = obj.matrix_world.copy() @ vertex.co
            intersection = tool.Cad.point_on_edge(world_vertex, (ray_target, loc))
            distance = (world_vertex - intersection).length
            if distance < 0.2:
                points.append([distance - stick_factor, (world_vertex, "Vertex")])

        for edge in bm.edges:
            v1 = edge.verts[0].co
            v2 = edge.verts[1].co
            world_v1 = obj.matrix_world.copy() @ v1
            world_v2 = obj.matrix_world.copy() @ v2
            division_point = (world_v1 + world_v2) / 2  # TODO Make it work for different divisions

            intersection = tool.Cad.point_on_edge(division_point, (ray_target, loc))
            distance = (division_point - intersection).length
            if distance < 0.2:
                points.append([distance, (division_point, "Edge Center")])

            intersection = tool.Cad.intersect_edges_v2((ray_target, loc), (world_v1, world_v2))
            if intersection:
                if tool.Cad.is_point_on_edge(intersection[1], (world_v1, world_v2)):
                    distance = (intersection[1] - intersection[0]).length
                    if distance < 0.8:
                        points.append([distance + 4 * stick_factor, (intersection[1], "Edge")])

        bm.free()
        snapping_points = []
        sorted_points = sorted(points)
        for p in sorted_points:
            snapping_points.append(p[1])

        return snapping_points

    @classmethod
    def ray_cast_to_polyline(cls, context, event):
        region = context.region
        rv3d = context.region_data
        mouse_pos = event.mouse_region_x, event.mouse_region_y
        ray_origin, ray_target, ray_direction = cls.get_viewport_ray_data(context, event)

        try:
            loc = view3d_utils.region_2d_to_location_3d(region, rv3d, mouse_pos, ray_direction)
        except:
            loc = Vector((0, 0, 0))

        polyline_data = bpy.context.scene.BIMModelProperties.polyline_point
        polyline_points = []
        for point_data in polyline_data:
            point = Vector((point_data.x, point_data.y, point_data.z))

            intersection, _ = mathutils.geometry.intersect_point_line(point, ray_target, loc)
            distance = (point - intersection).length
            if distance < 0.2:
                polyline_points.append((point, "Vertex"))

        return polyline_points

    @classmethod
    def ray_cast_to_plane(cls, context, event, plane_origin, plane_normal):
        region = context.region
        rv3d = context.region_data
        mouse_pos = event.mouse_region_x, event.mouse_region_y
        ray_origin, ray_target, ray_direction = cls.get_viewport_ray_data(context, event)

        default_container_elevation = tool.Ifc.get_object(tool.Root.get_default_container()).location.z
        intersection = Vector((0, 0, default_container_elevation))
        try:
            loc = view3d_utils.region_2d_to_location_3d(region, rv3d, mouse_pos, ray_direction)
            intersection = mathutils.geometry.intersect_line_plane(ray_target, loc, plane_origin, plane_normal)
        except:
            intersection = Vector((0, 0, default_container_elevation))

        if intersection == None:
            intersection = Vector((0, 0, default_container_elevation))

        return intersection
