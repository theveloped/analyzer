import matplotlib.pyplot as plt

from svgpathtools import Path, Line, svg2paths

from svgwrite import Drawing



# Estimating the parameters from the image since the actual values are not given

# Estimated values in millimeters for demonstration

diameter = 10.0

radius = diameter / 2

cutting_length = 30.0

shank_diameter = 5.0

shank_changeover_length = 5.0

shank_length = 20.0



# Holder dimensions as an array of tuples (position, diameter)

# These are estimated based on the proportions in the image

holder_dimensions = [(0, shank_diameter), (shank_changeover_length, shank_diameter),

                     (shank_changeover_length, diameter), (shank_length, diameter)]



# Function to draw the tool as an SVG

def draw_tool(dwg, holder_dims, cutting_len, dia, shank_dia):

    # Initial position

    pos_x = 100

    pos_y = 200

    tool_group = dwg.g(id='tool')



    # Draw the cutting part

    cutting_path = Path(Line((pos_x, pos_y), (pos_x, pos_y - cutting_len)))

    tool_group.add(dwg.path(d=Path(cutting_path).d(), stroke='green', fill='none', stroke_width=dia))



    # Update the current Y position after the cutting length

    current_y = pos_y - cutting_len



    # Draw each part of the holder

    for pos, dia in holder_dims:

        holder_path = Path(Line((pos_x, current_y), (pos_x, current_y - pos)))

        tool_group.add(dwg.path(d=Path(holder_path).d(), stroke='brown', fill='none', stroke_width=dia))

        current_y -= pos



    dwg.add(tool_group)



# Create SVG drawing

dwg = Drawing('milling_tool.svg', size=('200mm', '250mm'))

draw_tool(dwg, holder_dimensions, cutting_length, diameter, shank_diameter)



# Save SVG file

dwg.save()



'Milling tool SVG has been created.'


