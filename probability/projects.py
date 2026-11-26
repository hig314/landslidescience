"""The projects under /probability/, one entry each.

A project is a site-specific hazard analysis built in the ProbHaz repository
(github.com/hig314/ProbHaz) and copied here by its `publish/to_site.py`:
viewer code into static/probability/, the page body into
templates/probability/_viewer_body.html, data into data/probability/<id>/.

  published   False = signed-in accounts that may see restricted data only
              (inventory.auth.can_view_restricted), and the data is read from
              the PRIVATE bucket through this server. True = public, data on
              the public bucket. Publishing is: upload the data to the public
              bucket, then flip this. Never the other way round.
  template    a template that extends probability/base.html, for a project
              that needs more than the shared page. None = the shared page.
  dem         the lidar catalogue id of the surface the 3D view is drawn on.
  view        where the map opens.
"""

PROJECTS = {
    'homer': {
        'title': 'Homer shallow landslides',
        'place': 'Homer, Alaska',
        'blurb': 'Shallow landslides on the Homer bench and bluffs: terrain statistics on a 2 m '
                 'surface, the Alaska DGGS landslide inventory, and a reproduction of the DGGS '
                 'factor of safety as a continuous map.',
        'sub': 'Terrain statistics, the DGGS inventory and a reproduction of the DGGS factor of safety.',
        'published': False,
        'template': None,
        'dem': 'kbay_topobathy_v1',
        'view': {'center': [-151.53, 59.655], 'zoom': 12.3, 'pitch': 55, 'bearing': -20},
    },
}
